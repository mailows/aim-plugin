"""Start the AIM channel from the source bundled with this plugin.

The host runs this with the packages in requirements.txt available. The channel's own code is
in lib/ next to this file; its path is taken from where this file is, not from the environment,
because not every host that installs the plugin exports CLAUDE_PLUGIN_ROOT to the process - Grok
substitutes it into the command line and nowhere else.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from aimessenger.client.channel.server import main  # after the path above, on purpose

if __name__ == "__main__":
    main()
