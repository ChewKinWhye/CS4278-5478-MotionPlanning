#!/usr/bin/env python
import rospy
import numpy as np
import time
from geometry_msgs.msg import *
from nav_msgs.msg import *
from sensor_msgs.msg import *
from const import *
from math import *
import copy
import argparse
import matplotlib.pyplot as plt
import heapq
import json
import os
ROBOT_SIZE = 0.2552  # width and height of robot in terms of stage unit


def dump_action_table(action_table, filename):
    """dump the MDP policy into a json file

    Arguments:
        action_table {dict} -- your mdp action table. It should be of form {'1,2,0': (1, 0), ...}
        filename {str} -- output filename
    """
    tab = dict()
    for k, v in action_table.items():
        key = [str(i) for i in k]
        key = ','.join(key)
        tab[key] = v

    with open(filename, 'w') as fout:
        json.dump(tab, fout)


class Planner:
    def __init__(self, world_width, world_height, world_resolution, inflation_ratio=3, com=0):
        """init function of the base planner. You should develop your own planner
        using this class as a base.

        For standard mazes, width = 200, height = 200, resolution = 0.05. 
        For COM1 map, width = 2500, height = 983, resolution = 0.02

        Arguments:
            world_width {int} -- width of map in terms of pixels
            world_height {int} -- height of map in terms of pixels
            world_resolution {float} -- resolution of map

        Keyword Arguments:
            inflation_ratio {int} -- [description] (default: {3})
        """
        rospy.init_node('planner')
        self.map = None
        self.pose = None
        self.goal = None
        self.path = None
        self.action_seq = None  # output
        self.aug_map = None  # occupancy grid with inflation
        self.action_table = {}
        self.com = com
        self.crash_penalty = -20
        self.world_width = world_width
        self.world_height = world_height
        self.resolution = world_resolution
        self.inflation_ratio = inflation_ratio
        self.distance_penalty_normalization = (self.world_height * self.resolution + self.world_width * self.resolution) * 10

        print("Calling map_callback()")
        self.map_callback()

        self.sb_obs = rospy.Subscriber('/scan', LaserScan, self._obs_callback)
        self.sb_pose = rospy.Subscriber(
            '/base_pose_ground_truth', Odometry, self._pose_callback)
        # print("self.sb_pose: ", self.sb_pose)
        self.sb_goal = rospy.Subscriber(
            '/move_base_simple/goal', PoseStamped, self._goal_callback)
        # print("self.sb_goal: ", self.sb_goal)
        self.controller = rospy.Publisher(
            '/mobile_base/commands/velocity', Twist, queue_size=10)
        rospy.sleep(1)

    def map_callback(self):
        """Get the occupancy grid and inflate the obstacle by some pixels. You should implement the obstacle inflation yourself to handle uncertainty.
        """
        self.map = rospy.wait_for_message('/map', OccupancyGrid).data
        print("Obtained map from simulator")
        map_values = {}
        for value in self.map:
            if value in map_values:
                map_values[value] += 1
            else:
                map_values[value] = 1
        print(map_values)
        # TODO: FILL ME! implement obstacle inflation function and define self.aug_map = new_mask
        if self.com and os.path.exists("com1_augmap.npy"):
            self.aug_map = np.load("com1_augmap.npy")
        else:
            self.map = np.array(self.map).reshape((self.world_height, self.world_width))
            # you should inflate the map to get self.aug_map
            self.aug_map = copy.deepcopy(self.map)
            pixel_buffer = int(ROBOT_SIZE / resolution * self.inflation_ratio)
            for i in range(self.world_height):
                for ii in range(self.world_width):
                    if self.map[i, ii] == 100 or i == 0 or i == self.world_height-1 or ii == 0 or ii == self.world_width-1:
                        top_index = max(0, i - pixel_buffer)
                        bottom_index = min(self.world_height, i + pixel_buffer)
                        left_index = max(0, ii - pixel_buffer)
                        right_index = min(self.world_width, ii + pixel_buffer)
                        for height_inflate in range(top_index, bottom_index):
                            for width_inflate in range(left_index, right_index):
                                self.aug_map[height_inflate, width_inflate] = 100
        if self.com and not os.path.exists("com1_augmap.npy"):
            np.save('com1_augmap.npy', self.aug_map)

        self.map = self.map[::-1]
        print(len(self.map))
        # plt.imshow(self.map, cmap='gray', vmin=-1, vmax=100, interpolation='none')
        # plt.show()
        # plt.imshow(self.aug_map, cmap='gray', vmin=-1, vmax=100, interpolation='none')
        # plt.show()

    def _pose_callback(self, msg):
        """get the raw pose of the robot from ROS

        Arguments:
            msg {Odometry} -- pose of the robot from ROS
        """
        self.pose = msg
        #print("self.pose: ", self.pose)

    def _goal_callback(self, msg):
        self.goal = msg
        #print("self.goal: ", self.goal)
        self.generate_plan()

    def _get_goal_position(self):
        goal_position = self.goal.pose.position
        return (goal_position.x, goal_position.y)

    def set_goal(self, x, y, theta=0):
        """set the goal of the planner

        Arguments:
            x {int} -- x of the goal
            y {int} -- y of the goal

        Keyword Arguments:
            theta {int} -- orientation of the goal; we don't consider it in our planner (default: {0})
        """
        a = PoseStamped()
        a.pose.position.x = x
        a.pose.position.y = y
        a.pose.orientation.z = theta
        self.goal = a

    def _obs_callback(self, msg):
        """get the observation from ROS; currently not used in our planner; researve for the next assignment

        Arguments:
            msg {LaserScan} -- LaserScan ROS msg for observations
        """
        self.last_obs = msg

    def _d_from_goal(self, pose):
        """compute the distance from current pose to the goal; only for goal checking

        Arguments:
            pose {list} -- robot pose

        Returns:
            float -- distance to the goal
        """
        goal = self._get_goal_position()
        return sqrt((pose[0] - goal[0])**2 + (pose[1] - goal[1])**2)

    def _check_goal(self, pose):
        """Simple goal checking criteria, which only requires the current position is less than 0.25 from the goal position. The orientation is ignored

        Arguments:
            pose {list} -- robot post

        Returns:
            bool -- goal or not
        """
        if self._d_from_goal(pose) < 0.25:
            return True
        else:
            return False

    def create_control_msg(self, x, y, z, ax, ay, az):
        """a wrapper to generate control message for the robot.

        Arguments:
            x {float} -- vx
            y {float} -- vy
            z {float} -- vz
            ax {float} -- angular vx
            ay {float} -- angular vy
            az {float} -- angular vz

        Returns:
            Twist -- control message
        """
        message = Twist()
        message.linear.x = x
        message.linear.y = y
        message.linear.z = z
        message.angular.x = ax
        message.angular.y = ay
        message.angular.z = az
        return message

    def get_current_state_value(self, state):
        if self.collision_checker(state[0], state[1]):
            current_state_value = self.crash_penalty
        elif self._check_goal(state):
            current_state_value = 20
        else:
            current_state_value = -(self._d_from_goal(state) / self.distance_penalty_normalization)
        return current_state_value

    def get_next_state_value(self, next_state):
        if next_state is None:
            next_state_value = self.crash_penalty
        else:
            next_state_value = self.state_values[next_state]
        return next_state_value

    def get_forward_action_value(self, state):
        next_state_value = 0
        x, y, theta = state
        next_state = self.discrete_motion_predict(x, y, theta, 1, 0)
        next_state_value += self.get_next_state_value(next_state) * 0.9

        next_state = self.discrete_motion_predict(x, y, theta, np.pi / 2, 1)
        next_state_value += self.get_next_state_value(next_state) * 0.05

        next_state = self.discrete_motion_predict(x, y, theta, np.pi / 2, -1)
        next_state_value += self.get_next_state_value(next_state) * 0.05
        return next_state_value

    def generate_plan(self):
        """TODO: FILL ME! This function generates the plan for the robot, given a goal.
        You should store the list of actions into self.action_seq.

        In discrete case (task 1 and task 3), the robot has only 4 heading directions
        0: east, 1: north, 2: west, 3: south

        Each action could be: (1, 0) FORWARD, (0, 1) LEFT 90 degree, (0, -1) RIGHT 90 degree

        In continuous case (task 2), the robot can have arbitrary orientations

        Each action could be: (v, \omega) where v is the linear velocity and \omega is the angular velocity
        """
        print("Generating Plan")

        self.action_table = {}
        actions = [(1, 0), (0, 1), (0, -1)]
        states = []
        for height in range(0, int(self.world_height * self.resolution)+1):
            for width in range(0, int(self.world_width * self.resolution)+1):
                for theta in range(4):
                    states.append((width, height, theta))
        print(len(states))
        # Initialize all values to 0
        self.state_values = {}
        for state in states:
            self.state_values[state] = 0
        if self.com:
            steps = 100
        else:
            steps = 200
        # 200 update steps
        for i in range(steps):
            # Update every state at each step
            for state in states:
                state_value = -10000000
                # Loop through all actions to find the highest value
                # value = current_state_value + next_state_value
                for action in actions:
                    current_state_value = self.get_current_state_value(state)
                    # Obtain value for left and right
                    if action == (0, 1) or action == (0, -1):
                        # Obtain action deterministically
                        v, w = action
                        x, y, theta = state
                        next_state = self.discrete_motion_predict(x, y, theta, v, w)
                        next_state_value = self.get_next_state_value(next_state)
                    else:
                        # Obtain next state value probabilistically
                        next_state_value = self.get_forward_action_value(state)

                    total_value = current_state_value + 0.95 * next_state_value
                    if total_value > state_value:
                        state_value = total_value
                self.state_values[state] = state_value

        for state in states:
            max_value, best_action = -10000000, None
            for action in actions:
                if action == (0, 1) or action == (0, -1):
                    # Obtain action deterministically
                    v, w = action
                    x, y, theta = state
                    next_state = self.discrete_motion_predict(x, y, theta, v, w)
                    next_state_value = self.get_next_state_value(next_state)
                else:
                    # Obtain action probabilistically
                    next_state_value = self.get_forward_action_value(state)
                if next_state_value > max_value:
                    max_value = next_state_value
                    best_action = action
            self.action_table["{},{},{}".format(state[0], state[1], state[2] % 4)] = best_action
        print("Generated plan")

    def get_current_continuous_state(self):
        """Our state is defined to be the tuple (x,y,theta). 
        x and y are directly extracted from the pose information. 
        Theta is the rotation of the robot on the x-y plane, extracted from the pose quaternion. For our continuous problem, we consider angles in radians

        Returns:
            tuple -- x, y, \theta of the robot
        """
        x = self.pose.pose.pose.position.x
        y = self.pose.pose.pose.position.y
        orientation = self.pose.pose.pose.orientation
        ori = [orientation.x, orientation.y, orientation.z,
               orientation.w]

        phi = np.arctan2(2 * (ori[0] * ori[1] + ori[2] * ori[3]), 1 - 2 *
                         (ori[1] ** 2 + ori[2] ** 2))
        return (x, y, phi)

    def get_current_discrete_state(self):
        """Our state is defined to be the tuple (x,y,theta). 
        x and y are directly extracted from the pose information. 
        Theta is the rotation of the robot on the x-y plane, extracted from the pose quaternion. For our continuous problem, we consider angles in radians

        Returns:
            tuple -- x, y, \theta of the robot in discrete space, e.g., (1, 1, 1) where the robot is facing north
        """
        x, y, phi = self.get_current_continuous_state()
        def rd(x): return int(round(x))
        return rd(x), rd(y), rd(phi / (np.pi / 2))

    def collision_checker(self, x, y):
        """TODO: FILL ME!
        You should implement the collision checker.
        Hint: you should consider the augmented map and the world size
        
        Arguments:
            x {float} -- current x of robot
            y {float} -- current y of robot
        
        Returns:
            bool -- True for collision, False for non-collision
        """
        if int(y/self.resolution) <= 0 or int(y/self.resolution) >= self.world_height or \
           int(x/self.resolution) <= 0 or int(x/self.resolution) >= self.world_width or \
           self.aug_map[int(y/self.resolution), int(x/self.resolution)] == 100:
            return True
        return False

    def motion_predict(self, x, y, theta, v, w, dt=0.5, frequency=10):
        """predict the next pose of the robot given controls. Returns None if the robot collide with the wall
        The robot dynamics are provided in the homework description

        Arguments:
            x {float} -- current x of robot
            y {float} -- current y of robot
            theta {float} -- current theta of robot
            v {float} -- linear velocity 
            w {float} -- angular velocity

        Keyword Arguments:
            dt {float} -- time interval. DO NOT CHANGE (default: {0.5})
            frequency {int} -- simulation frequency. DO NOT CHANGE (default: {10})

        Returns:
            tuple -- next x, y, theta; return None if has collision
        """
        num_steps = int(dt * frequency)
        dx = 0
        dy = 0
        for i in range(num_steps):
            if w != 0:
                dx = - v / w * np.sin(theta) + v / w * \
                    np.sin(theta + w / frequency)
                dy = v / w * np.cos(theta) - v / w * \
                    np.cos(theta + w / frequency)
            else:
                dx = v*np.cos(theta)/frequency
                dy = v*np.sin(theta)/frequency
            x += dx
            y += dy

            if self.collision_checker(x, y):
                return None
            theta += w / frequency
        return x, y, theta

    def discrete_motion_predict(self, x, y, theta, v, w, dt=0.5, frequency=10):
        """discrete version of the motion predict. Note that since the ROS simulation interval is set to be 0.5 sec
        and the robot has a limited angular speed, to achieve 90 degree turns, we have to execute two discrete actions
        consecutively. This function wraps the discrete motion predict.

        Please use it for your discrete planner.

        Arguments:
            x {int} -- current x of robot
            y {int} -- current y of robot
            theta {int} -- current theta of robot
            v {int} -- linear velocity
            w {int} -- angular velocity (0, 1, 2, 3)

        Keyword Arguments:
            dt {float} -- time interval. DO NOT CHANGE (default: {0.5})
            frequency {int} -- simulation frequency. DO NOT CHANGE (default: {10})

        Returns:
            tuple -- next x, y, theta; return None if has collision
        """
        w_radian = w * np.pi/2
        first_step = self.motion_predict(x, y, theta*np.pi/2, v, w_radian)
        if first_step:
            second_step = self.motion_predict(
                first_step[0], first_step[1], first_step[2], v, w_radian)
            if second_step:
                return (round(second_step[0]), round(second_step[1]), round(second_step[2] / (np.pi / 2)) % 4)
        return None

    def publish_control(self):
        """publish the continuous controls
        """
        for action in self.action_seq:
            msg = self.create_control_msg(action[0], 0, 0, 0, 0, action[1])
            self.controller.publish(msg)
            rospy.sleep(0.6)

    def publish_discrete_control(self):
        """publish the discrete controls
        """
        for action in self.action_seq:
            msg = self.create_control_msg(
                action[0], 0, 0, 0, 0, action[1]*np.pi/2)
            self.controller.publish(msg)
            rospy.sleep(0.6)
            self.controller.publish(msg)
            rospy.sleep(0.6)

    def publish_stochastic_control(self):
        """publish stochastic controls in MDP.
        In MDP, we simulate the stochastic dynamics of the robot as described in the assignment description.
        Please use this function to publish your controls in task 3, MDP. DO NOT CHANGE THE PARAMETERS :)
        We will test your policy using the same function.
        """
        current_state = self.get_current_discrete_state()
        while not self._check_goal(current_state):
            current_state = self.get_current_discrete_state()
            action = self.action_table["{},{},{}".format(current_state[0],
                                                         current_state[1], current_state[2] % 4)]
            print("Actual action", action)
            if action == (1, 0) or action == [1, 0]:
                r = np.random.rand()
                if r < 0.9:
                    action = (1, 0)
                elif r < 0.95:
                    action = (np.pi / 2, 1)
                else:
                    action = (np.pi / 2, -1)
            print("Sending actions:", action[0], action[1] * np.pi / 2)
            msg = self.create_control_msg(action[0], 0, 0, 0, 0, action[1] * np.pi / 2)
            self.controller.publish(msg)
            rospy.sleep(0.6)
            self.controller.publish(msg)
            rospy.sleep(0.6)
            time.sleep(1)
        print("Reached Goal")

if __name__ == "__main__":
    # TODO: You can run the code using the code below
    print("Starting algorithm")
    parser = argparse.ArgumentParser()
    parser.add_argument('--goal', type=str, default='1,8',
                        help='goal position')
    parser.add_argument('--com', type=int, default=0,
                        help="if the map is com1 map")
    parser.add_argument('--map', type=str, default="map1",
                        help="if the map is com1 map")
    args = parser.parse_args()

    try:
        goal = [int(pose) for pose in args.goal.split(',')]
    except:
        raise ValueError("Please enter correct goal format")
    print("Goal:", goal)
    if args.com:
        width = 2500
        height = 983
        resolution = 0.02
    else:
        width = 200
        height = 200
        resolution = 0.05
    print("Finished parsing arguments")

    # TODO: You should change this value accordingly
    inflation_ratio = 2
    planner = Planner(width, height, resolution, inflation_ratio=inflation_ratio, com=args.com)
    print("Done Initialization")

    planner.set_goal(goal[0], goal[1])
    if planner.goal is not None:
        planner.generate_plan()

    # You could replace this with other control publishers
    save_path = "controls/DSPA_{}_{}_{}.json".format(args.map, goal[0], goal[1])
    dump_action_table(planner.action_table, save_path)
    planner.publish_stochastic_control()
    # save your action sequence
    # result = np.array(planner.action_seq)
    # np.savetxt("actions_continuous.txt", result, fmt="%.2e")

    # for MDP, please dump your policy table into a json file


    # spin the ros
    rospy.spin()
