from utils.ssl.Navigation import Navigation
from utils.ssl.base_agent import BaseAgent
from utils.grid_converter import GridConverter
import numpy as np
from typing import List, Set, Tuple
from utils.Point import Point
from rsoccer_gym.Entities import Robot
from PathPlanning.d_star_lite import DStarLite
from PathPlanning.velocity_obstacles import VelocityObstacle

class DStarLiteAgent(BaseAgent):
    def __init__(self, id=0, yellow=False):
        super().__init__(id, yellow)
        self.robot_radius = 0.06
        self.planned_path = []
        self.grid_size = 35
        self.grid_converter = GridConverter(field_length=8.0, field_width=5.0, grid_size=self.grid_size)
        self.grid = None
        self.dstar = None
        self.previous_obstacle_cells: Set[Tuple[int, int]] = set()
        self.previous_target = None
        self.render_path = []
        self.velocity_obstacle = VelocityObstacle(robot_radius=self.robot_radius)
        self.max_speed = 4.0
        self.current_target = None
        self.assigned_target = None

    def check_collisions(self, desired_velocity: Point) -> Point:
        """
        Check for potential collisions and adjust velocity if needed.
        """
        own_pos = (self.robot.x, self.robot.y)
        own_vel = (desired_velocity.x, desired_velocity.y)
        
        # Check collisions with all other robots
        for opponent in self.opponents.values():
            other_pos = (opponent.x, opponent.y)
            other_vel = (opponent.v_x, opponent.v_y)
            combined_radius = self.robot_radius * 2 + 0.4 # Assuming same radius for all robots
            
            # Check if collision is predicted
            if self.velocity_obstacle.get_velocity_obstacle(
                own_pos, own_vel, other_pos, other_vel, 
                combined_radius, time_horizon=6.0
            ):
                # Get avoidance velocity
                new_vel = self.velocity_obstacle.get_avoidance_velocity(
                    own_vel, own_pos, other_pos, other_vel, 
                    max_speed=self.max_speed
                )
                return Point(new_vel[0], new_vel[1])
                
        # No collision predicted, return original velocity
        return desired_velocity

    def goTo(self, point: Point):
        target_velocity, target_angle_velocity = Navigation.goToPoint(
            self.robot, point
        )
        
        # Check for collisions and adjust velocity if needed
        safe_velocity = self.check_collisions(target_velocity)
        
        self.set_vel(safe_velocity)
        self.set_angle_vel(target_angle_velocity)   
        
    def step(self, 
             self_robot: Robot, 
             opponents: dict[int, Robot] = dict(), 
             teammates: dict[int, Robot] = dict(), 
             targets: list[Point] = [], 
             pursued_targets: list[bool] = [],
             keep_targets=False,
             path=None) -> Robot:
        
        # Target (Point), hasPursuer (bool) -> Indicates if the robot is a pursuer of target i 
        # self.targets = [[target, pursued] for target, pursued in zip(targets, pursued_targets)]
        # target point (coordinates from goals) 
        self.targets = targets.copy() 

        # list of bools, True if the robot is a pursuer of target i
        self.pursued_targets = pursued_targets.copy()


        self.robot = self_robot
        self.opponents = opponents.copy()
        self.teammates = teammates.copy()

        pursued_target = self.decision(opponents)
        self.post_decision()
        

    
        return Robot(id=self.id, yellow=self.yellow,
                    v_x=self.next_vel.x, v_y=self.next_vel.y, v_theta=self.angle_vel), pursued_target

    def detect_changes(self, old_grid: np.ndarray, new_grid: np.ndarray) -> List[Tuple[int, int]]:
        """
        Detect changes in the grid between the previous and current state.

        Args:
            old_grid: Previous grid state.
            new_grid: Current grid state.

        Returns:
            List of cells that have changed.
        """
        if old_grid is None:
            # If old_grid is None, assume all cells in new_grid have changed
            rows, cols = new_grid.shape
            return [(x, y) for x in range(rows) for y in range(cols)]
        
        changes = []
        rows, cols = old_grid.shape  # Get grid dimensions dynamically
        for x in range(rows):
            for y in range(cols):
                if old_grid[x, y] != new_grid[x, y]:
                    changes.append((x, y))
        return changes


    def get_followed_target(self):
        for target, is_pursuer in self.targets:
            if is_pursuer:
                return target

    def set_current_target(self, target):
        self.current_target = target

    def decision(self, opponents: dict[int, Robot] = dict()):
        if not self.targets:
            return self.pursued_targets

        if self.current_target is None:
            available_targets = []
            available_target_indices = []
            
            for i, (target, is_pursued) in enumerate(zip(self.targets, self.pursued_targets)):
                if not is_pursued:
                    available_targets.append(target)
                    available_target_indices.append(i)
            
            if available_targets:
                # Calculate distances to all available targets
                distances = [Navigation.distance_to_point(self.robot, target) 
                           for target in available_targets]
                
                # Find the closest available target
                closest_idx = np.argmin(distances)
                target_idx = available_target_indices[closest_idx]
                
                # Assign this robot to the target
                self.current_target = self.targets[target_idx]
                self.pursued_targets[target_idx] = True
            else:
                # No available targets, stay in place
                self.current_target = Point(self.robot.x, self.robot.y)

        if self.current_target and not Navigation.distance_to_point(self.robot, self.current_target) < 10e-3:
            

            current_grid, start_grid, goal_grid = self.create_grids(self.current_target, opponents)
            changes = self.detect_changes(self.grid, current_grid)

            if not self.dstar or self.hasTargetChanged(self.current_target):
                print(f"target: {self.current_target} robot pos {self.robot.x, self.robot.y} {Navigation.distance_to_point(self.robot, self.current_target)}")
                print("")
                self.dstar = DStarLite(grid=current_grid, start=start_grid, goal=goal_grid, id=self.id)
                self.dstar.compute_shortest_path()
            else:
                for cell in changes:
                    self.dstar.grid[cell] = current_grid[cell]
                    self.dstar.update_vertex(cell)
                self.dstar.compute_shortest_path()
            
            path = self.dstar.reconstruct_path()
            
            # Convert to continuous coordinates
            continuous_path = [
                self.grid_converter.grid_to_continuous(grid_x, grid_y)
                for grid_x, grid_y in path
            ]

            # Move towards next point
            if len(continuous_path) > 1:
                next_point = Point(*continuous_path[1])
            else:
                next_point = self.current_target
            
            self.goTo(next_point)
            self.dstar.start = self.grid_converter.continuous_to_grid(self.robot.x, self.robot.y)
            
            self.planned_path = continuous_path
            self.render_path = [Point(x, y) for x, y in continuous_path]
            self.previous_target = self.current_target
            self.grid = current_grid

            if self.reachedWayPoint(self.current_target) and self.current_target != Point(self.robot.x, self.robot.y):
            #    Reset target and paths
                self.current_target = None  
                self.planned_path = []
                self.render_path = []

        return self.pursued_targets

    def reachedWayPoint(self, point: Point):
        return Navigation.distance_to_point(self.robot, point) < 0.05

    def hasTargetChanged(self, current_target):
        return  self.previous_target is None \
        or abs(current_target.x - self.previous_target.x) > 1e-6 \
        or abs(current_target.y - self.previous_target.y) > 1e-6
        

    def create_grids(self, current_target, opponents):
        current_grid = self.grid_converter.create_grid(opponents, self.robot_radius + 0.1)

        start_grid = self.grid_converter.continuous_to_grid(
            self.robot.x, self.robot.y
        )
        goal_grid = self.grid_converter.continuous_to_grid(
            current_target.x, current_target.y
        )
        return current_grid, start_grid, goal_grid


    def post_decision(self):
        pass

    def get_planned_path(self):
        """Return the current planned path for rendering"""
        return self.render_path