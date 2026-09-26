# /// script
# requires-python = ">=3.14"
# dependencies = [
#   "mcp==2.2.0",
#   "pynacl==1.6.2",
#   "pydantic==2.13.5",
#   "python-ulid==4.0.1",
#   "websockets==17.1",
# ]
# ///
"""Start the AIM channel from the source bundled with this plugin.

The host runs `uv run --locked --script` on this file: the dependencies above are resolved from
aim_channel.py.lock next to it, hashes and all, and uv refuses to run if the two disagree. The
channel's own code is in lib/ next to this file. Its path is taken from where this file is, not
from the environment, because not every host that installs the plugin exports
CLAUDE_PLUGIN_ROOT to the process - Grok substitutes it into the command line and nowhere else.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from aimessenger.client.channel.server import main  # after the path above, on purpose

if __name__ == "__main__":
    main()
