from tools import CompositeMetaClass

from .alias import Alias
from .backup import Backup
from .boosterrole import BoosterRole
from .command import CommandManagement
from .disboard import Disboard
from .gallery import Gallery
from .logging import Logging
from .publisher import Publisher
from .roles import Roles
from .security import AntiNuke, AntiRaid
from .starboard import Starboard
from .statistics import Statistics
from .sticky import Sticky
from .system import System
from .thread import Thread
from .ticket import Ticket
from .timer import Timer

from .level import Level
from .trigger import Trigger
from .vanity import Vanity
from .voicemaster import VoiceMaster
from .webhook import Webhook
from .whitelist import Whitelist


class Extended(
    Alias,
    Roles,
    Timer,
    Ticket,
    Level,
    Vanity,
    Backup,
    Sticky,
    Thread,
    System,
    Webhook,
    Trigger,
    Gallery,
    Logging,
    Disboard,
    AntiRaid,
    AntiNuke,
    Publisher,
    Whitelist,
    Starboard,
    Statistics,
    VoiceMaster,
    BoosterRole,
    CommandManagement,
    metaclass=CompositeMetaClass,
):
    """
    Join all extended config cogs into one.
    """
