from eventmanager import Evt
from .plugin import RvLive

def initialize(rhapi):
    RvLive_plugin = RvLive(rhapi)
    rhapi.events.on(Evt.STARTUP, RvLive_plugin.init_plugin)