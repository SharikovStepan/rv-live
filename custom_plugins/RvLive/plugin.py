import logging
import uuid
import base64
import time
import json
from sqlalchemy.ext.declarative import DeclarativeMeta
from sqlalchemy import inspect
import requests
import gevent
from eventmanager import Evt
from RHUI import UIField, UIFieldType, UIFieldSelectOption

finalTypes_data = [
	 {"name": "No", "value": ""},
    {"name": "Quals", "value": "quals"},
   #  {"name": "Single Ellimination 8 pilots", "value": "single8"},
   #  {"name": "Single Ellimination 16 pilots", "value": "single16"},
    {"name": "Double Ellimination 16 pilots", "value": "double16"}
   #  {"name": "Double Ellimination 32 pilots", "value": "double32"},
   #  {"name": "FGDR Ellimination 8 pilots", "value": "fgdrSingle8"},
   #  {"name": "FGDR Ellimination 16 pilots", "value": "fgdrSingle16"},
   #  {"name": "Team Race", "value": "teamrace"}
]

finalsRoundsPerHeat = ['quals', '1', '2', '3', '4', '5']


class RvLive():
    finalTypes = []
    finalRounds = []

    for type in finalTypes_data:
        code = type['value']
        name = type["name"]
        option = UIFieldSelectOption(code, name)
        finalTypes.append(option)
    finalTypes_ui_fields = UIField(
        'finalType', "Tournament type", UIFieldType.SELECT, options=finalTypes, value="")

    for round in finalsRoundsPerHeat:
        option = UIFieldSelectOption(round, round)
        finalRounds.append(option)
    finalRounds_ui_fields = UIField(
        'finalRounds', "Rounds in Heat", UIFieldType.SELECT, options=finalRounds, value="quals")

    def __init__(self, rhapi):
        self.logger = logging.getLogger(__name__)
        self._rhapi = rhapi
        self.panel_name = "rv_live"

        self._rhapi.config.register_section('RvLive')

        self.keys = {
            "uuid": self._rhapi.config.get('RvLive', 'uuid') or "not_generated",
            "key": self._rhapi.config.get('RvLive', 'key') or "not_generated",
        }

        if self.keys["uuid"] == "not_generated":
            self.button_state = "generate"
            self.button_label = "Generate New Event"
        else:
            self.button_state = "clear"
            self.button_label = "Finish event"

        self.confirmation_start_time = None
        self.confirmation_timeout = 13

        self.API_ENDPOINT = "https://rh-results-viewer.vercel.app/api/upload"

    def init_plugin(self, args):
        self._rhapi.ui.register_panel(self.panel_name, "RV Live", "format")
        self._rhapi.server.enable_heartbeat_event()
        self._rhapi.events.on(Evt.DATABASE_RESET, self.on_database_reset)
        self._rhapi.events.on(Evt.CACHE_READY, self.on_results_update)
        self._rhapi.events.on(Evt.HEAT_ALTER, self.on_results_update)
        
        fields = self._rhapi.fields
        fields.register_raceclass_attribute(self.finalTypes_ui_fields)

        self._rhapi.fields.register_heat_attribute(
            UIField('duplicate', "Duplicate for heat (name)", UIFieldType.TEXT))
        self._rhapi.fields.register_heat_attribute(
            UIField('deleteRound', "Round to delete (number)", UIFieldType.TEXT))

        ui_rv_live_autoupload = UIField(
            name='rv_live_autoupload', label='Auto send', field_type=UIFieldType.CHECKBOX)
        
        ui_rv_live_frequency_set = UIField(
            name='rv_live_frequency_set', label='Enter frequency profile name', field_type=UIFieldType.TEXT)

        self._rhapi.fields.register_option(ui_rv_live_autoupload, "rv_live")
        self._rhapi.fields.register_option(ui_rv_live_frequency_set, "rv_live")
      #   self._rhapi.db.option("rv_live_autoupload")
        
        self.update_ui()

        self._rhapi.ui.register_quickbutton(
            "rv_live", "rv_live_manual_send", "Manual send", self.on_manual_update)




    def update_ui(self):
        self.update_key_display()
        self._rhapi.ui.register_quickbutton(
            self.panel_name,
            "main_action",
            self.button_label,
            self.main_button_handler
        )
        self._rhapi.ui.broadcast_ui("format")

    def get_duplicated_heats(self):
        heats = self._rhapi.db.heats
        duplicatedHeats = []
        for heat in heats:
            heatAttribute = self._rhapi.db.heat_attribute_value(
                heat.id, 'duplicate')
            if heatAttribute:
                classHeats = self._rhapi.db.heats_by_class(heat.class_id)
                for classHeat in classHeats:
                    if (classHeat.name == heatAttribute):
                        duplicatedHeats.append({'heatId': classHeat.id, 'heatName': classHeat.name, 'duplicateId': heat.id,
                                               'duplicateName': heat.name, 'classId': classHeat.class_id, 'classduplicateId': heat.class_id})
                        self.logger.info(
                            f"heat {classHeat.id}({classHeat.name}) has duplicate - id:{heat.id} ({heat.name})")
        return duplicatedHeats

    def get_deleted_rounds(self):
        heats = self._rhapi.db.heats
        deletedRounds = []
        for heat in heats:
            heatAttribute = self._rhapi.db.heat_attribute_value(
                heat.id, 'deleteRound')
            if heatAttribute:
                numbers = heatAttribute
                numbersList = [int(x.strip()) for x in numbers.split(',')]
                deletedRounds.append({'heatId': heat.id, 'deletedRoundNum': numbersList})
                self.logger.info(
                    f"In heat {heat.id} ({heat.name}) delete round Num:{numbersList}")
        return deletedRounds


    def get_no_results_heats(self):
        heats = self._rhapi.db.heats
        slotsList = []
        for heat in heats:
            results = self._rhapi.db.heat_results(heat)
            isResults = True
            if (not results):
                isResults = False
            if (True):
               #  self.logger.info(f"heatINFOOOO {json.dumps(heat, indent=4, cls=AlchemyEncoder)})")
                slotsByHeat = {'heatId': heat.id,'heatName': heat.name, 'classId': heat.class_id, 'isResults': isResults}
                slotPilotsList = []
                pilotSlots = self._rhapi.db.slots_by_heat(heat.id)
                for slot in pilotSlots:
                    slotStr = json.dumps(slot, indent=4, cls=AlchemyEncoder)
                    slotJson = json.loads(slotStr)
                    if (slotJson['pilot_id'] != 0):
                        pilotId = slotJson['pilot_id']
                        pilotInfo = self._rhapi.db.pilot_by_id(pilotId)
                        pilotInfoStr = json.dumps(
                            pilotInfo, indent=4, cls=AlchemyEncoder)
                        pilotInfoJson = json.loads(pilotInfoStr)
                        slotPilotsList.append(
                            {'id': pilotId, 'callsign': pilotInfoJson['callsign'],'nodeIndex': slotJson['node_index']})
                        slotsByHeat['pilots'] = slotPilotsList
                slotsList.append(slotsByHeat)
        return slotsList

    def get_channels(self):
        frequency = self._rhapi.db.frequencysets
        setName = self._rhapi.db.option("rv_live_frequency_set")
        currentFrequencySet = ''
        for set in frequency:
            setStr = json.dumps(set, indent=4, cls=AlchemyEncoder)
            setJson = json.loads(setStr)
            if (setJson['name'] == setName):
                currentFrequencySet = setJson
        return currentFrequencySet
          
       

    def update_key_display(self):
        uuid = self.keys['uuid']
        if self.button_state == "generate":
            markdown_str = "Generate new Event ⤵"
        elif self.button_state == "clear":
            markdown_str = f"**Event URL:**\n`https://rh-results-viewer.vercel.app/?uuid={uuid}`"
        elif self.button_state == "confirm":
            markdown_str = "Are you sure you want to FINISH your event?<br>This action cannot be undone and you will no longer be able to update it at this URL.<br>If you sure, press CONFIRM"

        self._rhapi.ui.register_markdown(
            self.panel_name, "Url_display", markdown_str)

    def main_button_handler(self, args):
        if self.button_state == "generate":
            self.generate_keys()
        elif self.button_state == "clear":
            self.prompt_clear_confirmation()
        elif self.button_state == "confirm":
            self._rhapi.config.set('RvLive', 'isFinished', True)
            gevent.spawn(self.send_data_to_api)

    def generate_keys(self):
        self.keys["uuid"] = str(base64.urlsafe_b64encode(
            uuid.uuid4().bytes).rstrip(b'=').decode('ascii'))
        self.keys["key"] = str(base64.urlsafe_b64encode(
            uuid.uuid4().bytes).rstrip(b'=').decode('ascii'))

        self._rhapi.config.set('RvLive', 'uuid', self.keys["uuid"])
        self._rhapi.config.set('RvLive', 'key', self.keys["key"])
        self._rhapi.config.set('RvLive', 'isFinished', False)

        gevent.spawn(self.send_data_to_api)

        self.button_state = "clear"
        self.button_label = "Finish event"
        self.confirmation_start_time = None
        self.update_ui()

        event_name = self._rhapi.db.option("eventName") or "Unnamed Event"
        self._rhapi.ui.message_notify(f"RV Live: '{event_name}' now in LIVE")
        self.logger.info("Generated new Event")

    def on_database_reset(self, args):
        self._rhapi.config.set('RvLive', 'isFinished', True)

    def on_results_update(self, args):
        if self._rhapi.db.option("rv_live_autoupload") == "1":
            if self.keys["uuid"] != "not_generated":
                self.logger.info("Manual send, sending data to API")
                gevent.spawn(self.send_data_to_api)

    def on_manual_update(self, args):
        if True:
            if self.keys["uuid"] != "not_generated":
                self.logger.info("Manual send, sending data to API")
                gevent.spawn(self.send_data_to_api)


    def send_data_to_api(self):
        try:
            if self._rhapi.config.get('RvLive', 'isFinished') != True:
                event_results = self._rhapi.eventresults.results
                event_name = self._rhapi.db.option("eventName") or "No titled"
                raceClasses = self._rhapi.db.raceclasses
                finalTypes = []
                noResultsHeats = ''
                duplicatedHeats = ''
                deletedRounds = ''
                channels = ''
                for raceClass in raceClasses:
                    currentType = self._rhapi.db.raceclass_attribute_value(
                        raceClass.id, "finalType")
                    finalType = {'raceClassId': raceClass.id,
                                 'finalType': currentType}
                    finalTypes.append(finalType)
                    if currentType != '':
                        noResultsHeats = self.get_no_results_heats()
                        duplicatedHeats = self.get_duplicated_heats()
                        deletedRounds = self.get_deleted_rounds()
                        channels = self.get_channels()

                payload = {
                    "uuid": self.keys["uuid"],
                    "key": self.keys["key"],
                    "isFinished": False,
                    "data": {
                        "finalTypesByClass": finalTypes,
                        "noResultsHeats": noResultsHeats or [],
                        "duplicatedHeats": duplicatedHeats or [],
                        "deletedRounds": deletedRounds or [],
                        "channels": channels,
                        "lastUpdate": int(time.time() * 1000),
                        "eventName": event_name,
                        "results": event_results
                    }
                }
            elif self._rhapi.config.get('RvLive', 'isFinished') == True:
                payload = {
                    "uuid": self.keys["uuid"],
                    "key": self.keys["key"],
                    "isFinished": True,
                }

            response = requests.post(
                self.API_ENDPOINT,
                json=payload,
                headers={'Content-Type': 'application/json'},
                timeout=5
            )
            if self._rhapi.config.get('RvLive', 'isFinished') == True:
                self.clear_keys()

            self.UI_Message(self._rhapi, response.text)

        except requests.exceptions.RequestException as e:
            error_msg = f"API connection failed: {str(e)}"
            self.logger.error(error_msg)
            self._rhapi.ui.message_alert(error_msg)
        except Exception as e:
            error_msg = f"Error preparing data: {str(e)}"
            self.logger.error(error_msg)
            self._rhapi.ui.message_alert(error_msg)

    def UI_Message(self, rhapi, text):
        try:
            response = json.loads(text)
            if isinstance(response, list):
                response = response[0]

            if isinstance(response, dict):
                if response.get('status') == 'error' or response.get('status') == 'failed' or 'error' in response:
                    error_msg = response.get('message') or response.get(
                        'error') or "Unknown error"
                    rhapi.ui.message_notify(f"RV Live Error: {error_msg}")
                    if response.get('status_code') == 410:
                        self._rhapi.ui.message_alert(
                            "RV LIVE:<br>"
                            f"Too long without updates({response.get('time')} hours)<br><br>"
                            "Your event on: <br>"
                            f"https://rh-results-viewer.vercel.app/?uuid={self.keys['uuid']}<br>"
                            "is FINISHED!<br><br>"
                            "Please, generate NEW"
                        )
                        self._rhapi.config.set('RvLive', 'isFinished', True)
                        gevent.spawn(self.send_data_to_api)
                        return

                if 'message' in response:
                    rhapi.ui.message_notify(f"RV Live: {response['message']}")
                    return

            rhapi.ui.message_notify("RV Live: Operation completed")

        except json.JSONDecodeError:
            rhapi.ui.message_notify("RV Live: Invalid server response")
        except Exception as e:
            self.logger.error(f"RV Live plugin error: {str(e)}")
            rhapi.ui.message_notify("RV Live: Processing error")

    def prompt_clear_confirmation(self):
        self.logger.info("Prompting for clear confirmation")
        self.button_state = "confirm"
        self.button_label = "!!! CONFIRM FINISH !!!"
        self.confirmation_start_time = time.time()
        self.update_ui()
        self._rhapi.events.on(
            Evt.HEARTBEAT, self.check_confirmation_timeout, priority=200, name='clearConfirm')

    def check_confirmation_timeout(self, args):
        if self.button_state == "confirm" and self.confirmation_start_time:
            elapsed = time.time() - self.confirmation_start_time
            if elapsed > self.confirmation_timeout:
                self.logger.info("Confirmation timeout expired")
                self.button_state = "clear"
                self.button_label = "Finish Event"
                self.confirmation_start_time = None
                self.update_ui()
                self._rhapi.events.off(Evt.HEARTBEAT, 'clearConfirm')
                self._rhapi.ui.message_notify("RV Live: Finish canceled")
        else:
            self.logger.debug(
                "Heartbeat event received (no confirmation pending)")

    def clear_keys(self):
        self.keys = {
            "uuid": "not_generated",
            "key": "not_generated"
        }

        self._rhapi.config.set('RvLive', 'uuid', "")
        self._rhapi.config.set('RvLive', 'key', "")
        self._rhapi.config.set('RvLive', 'isFinished', False)

        self.button_state = "generate"
        self.button_label = "Generate New Event"
        self.confirmation_start_time = None
        self.update_ui()

        self._rhapi.ui.message_notify(
            "RV Live: event is FINISHED. Please, generate NEW!")
        self.logger.info("Event cleared")
        self._rhapi.events.off(Evt.HEARTBEAT, 'clearConfirm')


class AlchemyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj.__class__, DeclarativeMeta):
            mapped_instance = inspect(obj)
            fields = {}
            for field in dir(obj):
                if field in [*mapped_instance.attrs.keys()]:
                    data = obj.__getattribute__(field)
                    if field != 'query' and field != 'query_class':
                        try:
                            json.dumps(data)
                            if field == 'frequencies':
                                fields[field] = json.loads(data)
                            elif field == 'enter_ats' or field == 'exit_ats':
                                fields[field] = json.loads(data)
                            else:
                                fields[field] = data
                        except TypeError:
                            fields[field] = None
            return fields
        return json.JSONEncoder.default(self, obj)