import json
from DSDA_planner import *

if __name__ == "__main__":
    # TODO: You can run the code using the code below
    with open("../../../files/goals.json") as f:
        data = json.load(f)
        print(data)
    parser = argparse.ArgumentParser()
    parser.add_argument('--goal', type=str, default='1,8',
                        help='goal position')
    parser.add_argument('--com', type=int, default=0,
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
    planner = Planner(width, height, resolution, inflation_ratio=inflation_ratio)
    print("Done Initialization")

    planner.set_goal(goal[0], goal[1])
    if planner.goal is not None:
        planner.generate_plan()

    # save your action sequence
    result = np.array(planner.action_seq)
    np.savetxt("actions_continuous.txt", result, fmt="%.2e")

    # for MDP, please dump your policy table into a json file
    # dump_action_table(planner.action_table, 'mdp_policy.json')
