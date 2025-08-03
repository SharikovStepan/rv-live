import logging
import uuid
import base64
import time
import json

import requests
import gevent

from eventmanager import Evt
from RHUI import UIField, UIFieldType, UIFieldSelectOption

class RvLive():
    def __init__(self, rhapi):
        self.logger = logging.getLogger(__name__)
        self._rhapi = rhapi
        self.panel_name = "rv_live"
      #   self._isFinished = False
        
        # Регистрируем секцию для сохраненных данных
        self._rhapi.config.register_section('RvLive')
        
        # Загружаем ключи из persistent configuration
        self.keys = {
            "uuid": self._rhapi.config.get('RvLive', 'uuid') or "not_generated",
            "key": self._rhapi.config.get('RvLive', 'key') or "not_generated",
        }
        
        # Определяем начальное состояние кнопки
        if self.keys["uuid"] == "not_generated":
            self.button_state = "generate"
            self.button_label = "Generate New Event"
        else:
            self.button_state = "clear"
            self.button_label = "Finish event"

        # Для отслеживания времени подтверждения
        self.confirmation_start_time = None
        self.confirmation_timeout = 13  # 30 секунд на подтверждение
        

        self.API_ENDPOINT = "https://rh-results-viewer.vercel.app/api/upload"
        
      #   self.logger.debug(f"Loaded keys: uuid={self.keys['uuid']}, key2={self.keys['key']}")

    def init_plugin(self, args):
        # Регистрируем панель
        self._rhapi.ui.register_panel(self.panel_name, "RV Live", "format")
        
        # Включаем генерацию событий HEARTBEAT
        self._rhapi.server.enable_heartbeat_event()
        
        # Регистрируем обработчик сердцебиения
      #   self._rhapi.events.on(Evt.HEARTBEAT, self.check_confirmation_timeout, priority=200)
        
        # Регистрируем обработчики для события очистки
        self._rhapi.events.on(Evt.DATABASE_RESET, self.on_database_reset)
        
        # Регистрируем обработчики для событий обновления результатов
        self._rhapi.events.on(Evt.CACHE_READY, self.on_results_update)
      #   self._rhapi.events.on(Evt.LAPS_SAVE, self.on_results_update)
      #   self._rhapi.events.on(Evt.RACE_FINISH, self.on_results_update)
        
        # Инициализируем UI
        self.update_ui()
      #   self.logger.info("RV Live plugin loaded")
      #   self.logger.info("Heartbeat event enabled")

    def update_ui(self):
        # Обновляем Markdown с ключами
        self.update_key_display()
        
        # Пересоздаем кнопку с текущим состоянием
        self._rhapi.ui.register_quickbutton(
            self.panel_name, 
            "main_action", 
            self.button_label, 
            self.main_button_handler
        )
        
        # Обновляем UI
        self._rhapi.ui.broadcast_ui("format")

    def update_key_display(self):
        # Форматируем ключи для Markdown
        uuid = self.keys['uuid']
        if  self.button_state == "generate":
            markdown_str = "Generate new Event ⤵"
        elif self.button_state == "clear":
            markdown_str = f"**Event URL:**\n`https://rh-results-viewer.vercel.app/?uuid={uuid}`"
        elif self.button_state == "confirm":
            markdown_str = "Are you sure you want to FINISH your event?<br>This action cannot be undone and you will no longer be able to update it at this URL.<br>If you sure, press CONFIRM"
           
        # Создаем Markdown-блоки для отображения ключей
        self._rhapi.ui.register_markdown(self.panel_name, "Url_display", markdown_str)
      #   self.logger.debug("Key display updated")

    def main_button_handler(self, args):
      #   self.logger.debug(f"Button pressed in state: {self.button_state}")
        if self.button_state == "generate":
            self.generate_keys()
        elif self.button_state == "clear":
            self.prompt_clear_confirmation()
        elif self.button_state == "confirm":
            self._rhapi.config.set('RvLive', 'isFinished', True)
            gevent.spawn(self.send_data_to_api)

    def generate_keys(self):
      #   self.logger.info("Generating new keys")
        # Генерация ключей
        self.keys["uuid"] = str(base64.urlsafe_b64encode(uuid.uuid4().bytes).rstrip(b'=').decode('ascii'))
        self.keys["key"] = str(base64.urlsafe_b64encode(uuid.uuid4().bytes).rstrip(b'=').decode('ascii'))
        
        # Сохранение в persistent configuration
        self._rhapi.config.set('RvLive', 'uuid', self.keys["uuid"])
        self._rhapi.config.set('RvLive', 'key', self.keys["key"])
        self._rhapi.config.set('RvLive', 'isFinished', False)
        
        # Асинхронная отправка ключей на API
        gevent.spawn(self.send_data_to_api)
        
        # Обновляем состояние кнопки
        self.button_state = "clear"
        self.button_label = "Finish event"
        self.confirmation_start_time = None
        self.update_ui()
        
        # Уведомление пользователя
        event_name = self._rhapi.db.option("eventName") or "Unnamed Event"
        self._rhapi.ui.message_notify(f"RV Live: '{event_name}' now in LIVE")
        self.logger.info("Generated new Event")

    def on_database_reset(self, args):
      #   self.logger.info("DATABASE_RESET triggered")
        self._rhapi.config.set('RvLive', 'isFinished', True)

    def on_results_update(self, args):
        """Обработчик событий, которые могут обновлять результаты"""
        
        if self.keys["uuid"] != "not_generated":
            self.logger.info("Results potentially updated, sending data to API")
            gevent.spawn(self.send_data_to_api)

    def send_data_to_api(self):
        """Асинхронная отправка данных на API Endpoint"""
        try:
            if self._rhapi.config.get('RvLive', 'isFinished') != True:
               # Получаем текущие результаты события
                event_results = self._rhapi.eventresults.results   
                # Получаем название события
                event_name = self._rhapi.db.option("eventName") or "No titled"
                # Формируем данные для отправки в правильной структуре
                payload = {
                    "uuid": self.keys["uuid"],
                    "key": self.keys["key"],
                    "isFinished": False,
                    "data": {
                        "lastUpdate":int(time.time() * 1000),
                        "eventName": event_name,
                        "results": event_results
                    }
                }
            elif self._rhapi.config.get('RvLive', 'isFinished')==True:
               payload = {
						"uuid": self.keys["uuid"],
                  "key": self.keys["key"],
                  "isFinished": True,
					}
            # self.logger.debug(f"Payload: {json.dumps(payload, indent=2)}")
            
            # Отправляем POST-запрос с правильными заголовками
            response = requests.post(
                self.API_ENDPOINT, 
                json=payload,
                headers={'Content-Type': 'application/json'},
                timeout=5
            )
            if self._rhapi.config.get('RvLive', 'isFinished')==True:
               self.clear_keys();
            # Подробное логирование ответа
            # self.logger.debug(f"API response: status={response.status_code}, text={response.text}")
            
            # Обрабатываем ответ и показываем уведомление пользователю
            # self.logger.info(f"Response from API: {response.text}")
            self.UI_Message(self._rhapi, response.text)
            
                
        except requests.exceptions.RequestException as e:
            error_msg = f"API connection failed: {str(e)}"
            self.logger.error(error_msg)
            # Показываем ошибку через UI
            self._rhapi.ui.message_alert(error_msg)
        except Exception as e:
            error_msg = f"Error preparing data: {str(e)}"
            self.logger.error(error_msg)
            self._rhapi.ui.message_alert(error_msg)

    def UI_Message(self, rhapi, text):
        """Показываем сообщение пользователю с обработкой JSON ответов"""
        try:
            # Пытаемся разобрать JSON ответ
            response = json.loads(text)
            if isinstance(response, list):
                response = response[0]  # Берем первый элемент, если ответ - массив
    
            # Основная логика обработки
            if isinstance(response, dict):
                # Сначала проверяем явные ошибки
                if response.get('status') == 'error' or response.get('status') == 'failed' or 'error' in response:
                    error_msg = response.get('message') or response.get('error') or "Unknown error"
                    rhapi.ui.message_notify(f"RV Live Error: {error_msg}")
                    if response.get('status_code') == 410:
                        self._rhapi.ui.message_alert(
									 "RV LIVE:<br>"
                            f"Too long without updates({response.get('time')} hours)<br><br>"
                            "Your event on: <br>"
                            f"https://rh-results-viewer.vercel.app/?uuid={self.keys["uuid"]}<br>"
                            "is FINISHED!<br><br>"
                            "Please, generate NEW"
                        )
                        self._rhapi.config.set('RvLive', 'isFinished', True)
                        gevent.spawn(self.send_data_to_api)
                        return
                
                # Затем успешные ответы
                if 'message' in response:
                    rhapi.ui.message_notify(f"RV Live: {response['message']}")
                    return
    
            # Если ничего не подошло - общее сообщение
            rhapi.ui.message_notify("RV Live: Operation completed")
    
        except json.JSONDecodeError:
            rhapi.ui.message_notify("RV Live: Invalid server response")
        except Exception as e:
            self.logger.error(f"RV Live plugin error: {str(e)}")
            rhapi.ui.message_notify("RV Live: Processing error")

    def prompt_clear_confirmation(self):
        self.logger.info("Prompting for clear confirmation")
        # Обновляем состояние кнопки
        self.button_state = "confirm"
        self.button_label = "!!! CONFIRM FINISH !!!"
        self.confirmation_start_time = time.time()
        self.update_ui()
        self._rhapi.events.on(Evt.HEARTBEAT, self.check_confirmation_timeout, priority=200)

    def check_confirmation_timeout(self, args):
        """Проверяем, не истекло ли время подтверждения"""
        if self.button_state == "confirm" and self.confirmation_start_time:
            elapsed = time.time() - self.confirmation_start_time
            self.logger.debug(f"Checking confirmation timeout: {elapsed:.1f}/{self.confirmation_timeout}s")
            
            if elapsed > self.confirmation_timeout:
                self.logger.info("Confirmation timeout expired")
                self.button_state = "clear"
                self.button_label = "Finish Event"
                self.confirmation_start_time = None
                self.update_ui()
                self._rhapi.events.off(Evt.HEARTBEAT, self.check_confirmation_timeout)
                
                self._rhapi.ui.message_notify("RV Live: Finish canceled")
                self.logger.info("Confirmation state reset automatically")
        else:
            # Для отладки: проверяем, вызывается ли этот обработчик
            self.logger.debug("Heartbeat event received (no confirmation pending)")

    def clear_keys(self):
        # Очистка ключей
        self.keys = {
            "uuid": "not_generated",
            "key": "not_generated"
        }
        
        # Очищаем persistent configuration
        self._rhapi.config.set('RvLive', 'uuid', "")
        self._rhapi.config.set('RvLive', 'key', "")
        self._rhapi.config.set('RvLive', 'isFinished', False)
        
        # Обновляем состояние кнопки
        self.button_state = "generate"
        self.button_label = "Generate New Event"
        self.confirmation_start_time = None
        self.update_ui()
        
        # Уведомление пользователя
        self._rhapi.ui.message_notify("RV Live: event is FINISHED. Please, generate NEW!")
        self.logger.info("Event cleared")
        self._rhapi.events.off(Evt.HEARTBEAT, self.check_confirmation_timeout)