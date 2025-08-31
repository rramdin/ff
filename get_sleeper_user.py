from sleeper_wrapper import User
import sys

if len(sys.argv) != 2:
    print("Usage:", sys.args[0], "<user_name>")
    sys.exit(1)
u = User(sys.argv[1])
print(u.get_user_id())
