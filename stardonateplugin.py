__id__ = "star_donation_bot_pg" # Вернул прежний ID плагина
__name__ = "Star Donation & Slots Bot"
__author__ = "@killwinparty & @PluginIDEbot"
__version__ = "1.0.0"
__description__ = """Прикольный бот для демонстрации фейковых донатов звездами и игры в "слоты" на них.
**ВНИМАНИЕ: Данная версия плагина демонстрирует НЕБЕЗОПАСНЫЙ способ обработки платежей (начисление звезд происходит на этапе pre_checkout_query, а не после successful_payment) и не должна использоваться в реальных проектах.**"""

__icon__ = "sPluginIDE/41" # Иконка игральной кости / рандома
__min_version__ = "11.12.0"

import threading
import time
import json
import requests
import random
from typing import Any, Optional, Dict, List

from base_plugin import BasePlugin, HookResult, HookStrategy
from ui.settings import Header, Input, Text, Divider, Switch
from ui.bulletin import BulletinHelper
from client_utils import get_last_fragment, get_user_config, send_message
from android_utils import run_on_ui_thread, log, OnClickListener
from ui.alert import AlertDialogBuilder # Импортируем для _reset_all_balances

# =====================================================================================
#   Star Donation & Slots Bot Plugin for exteraGram
#   Сгенерировано в PluginGRT - @PluginIDEbot #>^_^<#
# =====================================================================================

class StarDonationAndSlotsBotPlugin(BasePlugin):
    # Символы для слот-машины
    SLOT_SYMBOLS = ["🍒", "🍋", "🔔", "💰", "💎", "⭐"]

    def __init__(self):
        super().__init__()
        # --- Состояние бота ---
        self.bot_token: Optional[str] = None
        self.is_bot_running: bool = False
        self.bot_thread: Optional[threading.Thread] = None
        self.update_offset: int = 0
        self.host_id: Optional[int] = None # ID пользователя, запустившего плагин

        # --- Состояние донатов и слотов ---
        # { "user_id_str": {"name": "Username", "balance": 100}, ... }
        self.user_balances: Dict[str, Dict[str, Any]] = {}
        # pg_processing_payment_payload здесь уже не так критичен, если звезды начисляются сразу,
        # но может использоваться для отслеживания инициализированных платежей.
        self.pg_processing_payment_payload: Dict[str, Dict[str, Any]] = {} # query_id -> {'user_id': str, 'stars_amount': int}
        # Флаг для отслеживания пользователей, ожидающих ввод суммы доната или ставки для слотов
        # user_id -> "donate" | "slots_bet"
        self.pg_awaiting_input_type: Dict[str, str] = {} 

    def on_plugin_load(self):
        """Вызывается при загрузке плагина."""
        self.host_id = get_user_config().getClientUserId()
        self.bot_token = self.get_setting("bot_token", None)
        self.add_on_send_message_hook() # Для обработки команд из чата плагина
        self._load_user_balances()

        if self.bot_token:
            self.start_bot()
        else:
            log("[StarDonationAndSlotsBot] Токен бота не установлен. Бот не запущен.")
            run_on_ui_thread(lambda: BulletinHelper.show_info("Токен бота не установлен. Заполните в настройках.", get_last_fragment()))

    def on_plugin_unload(self):
        """Вызывается при выгрузке плагина."""
        self.stop_bot()

    def create_settings(self):
        """Создает UI настроек для плагина."""
        host_balance = self.user_balances.get(str(self.host_id), {'balance': 0})['balance']
        
        return [
            Header(text="Бот для донатов звездами и Слотов"),
            Input(
                key="bot_token", text="Токен вашего бота",
                subtext="Получите у @BotFather и вставьте сюда", icon="input_bot1",
                on_change=self._on_token_changed
            ),
            Divider(),
            Header(text="Ваш баланс (как хоста)"),
            Text(text=f"Всего пожертвовано: {host_balance} ⭐", icon="msg_giveaway_stars"),
            Text(text="Сбросить все балансы (для всех пользователей)", icon="msg_delete", red=True, on_click=self._reset_all_balances),
            Divider(),
            Header(text="Статус бота"),
            Text(text=f"Бот запущен: {'Да' if self.is_bot_running else 'Нет'}", icon="msg_info"),
            Divider(text="Команды в чате с ботом:\n/start - начать взаимодействие\n/balance - ваш баланс\n/leaderboard - таблица лидеров\n/slots - игра в слоты"),
        ]

    # --- Управление ботом ---

    def start_bot(self):
        if self.is_bot_running: return
        if not self.bot_token: return
        self.is_bot_running = True
        self.bot_thread = threading.Thread(target=self._bot_worker, daemon=True)
        self.bot_thread.start()
        log(f"[StarDonationAndSlotsBot] Бот запущен для хоста: {self.host_id}")
        run_on_ui_thread(lambda: BulletinHelper.show_success("Бот для донатов звезд и Слотов запущен!", get_last_fragment()))

    def stop_bot(self):
        if not self.is_bot_running: return
        self.is_bot_running = False
        self.bot_thread = None
        log("[StarDonationAndSlotsBot] Бот остановлен.")
        run_on_ui_thread(lambda: BulletinHelper.show_info("Бот для донатов звезд и Слотов остановлен.", get_last_fragment()))

    def _on_token_changed(self, new_token: str):
        self.stop_bot()
        self.bot_token = new_token.strip()
        self.set_setting("bot_token", self.bot_token)
        if self.bot_token: self.start_bot()
        self._refresh_settings_page()

    # --- Логика бота (в отдельном потоке) ---

    def _bot_worker(self):
        self.update_offset = 0
        while self.is_bot_running:
            try:
                updates = self._get_updates()
                for update in updates:
                    self.update_offset = update['update_id'] + 1
                    self._handle_update(update)
                if not updates: time.sleep(1)
            except requests.exceptions.RequestException as e:
                log(f"[StarDonationAndSlotsBot] Ошибка сети: {e}"); time.sleep(5)
            except Exception as e:
                log(f"[StarDonationAndSlotsBot] Ошибка в работе бота: {e}"); time.sleep(5)

    def _get_updates(self) -> list:
        if not self.bot_token: return []
        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
        # Разрешаем только сообщения, коллбэки и pre_checkout_query.
        # inline_query убраны по запросу.
        params = {'offset': self.update_offset, 'timeout': 10, 'allowed_updates': json.dumps(["message", "callback_query", "pre_checkout_query"])}
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        return r.json().get('result', [])

    def _handle_update(self, update: dict):
        if 'message' in update:
            message = update['message']
            if 'successful_payment' in message:
                self._handle_successful_payment(message)
            else:
                self._handle_message(message)
        elif 'callback_query' in update:
            self._handle_callback_query(update['callback_query'])
        elif 'pre_checkout_query' in update:
            self._handle_pre_checkout_query(update['pre_checkout_query'])
    
    # --- Обработчики команд (плагин в чате) ---

    def on_send_message_hook(self, account: int, params: Any):
        # Этот хук обрабатывает команды, введенные пользователем ПЛАГИНА (хостом)
        # в своем обычном чате, а не команды для бота.
        msg = params.message.strip().lower()
        if msg == ".sdresetall": # Команда хоста для полного сброса всех балансов
            self._reset_all_balances(None) # Вызов без View, просто для выполнения действия
            return HookResult(strategy=HookStrategy.CANCEL)
        return HookResult()

    # --- Обработчики обновлений (бот в Telegram) ---

    def _handle_message(self, message: dict):
        chat_id = message['chat']['id']
        user_id = str(message['from']['id'])
        user_name = message['from'].get('first_name', user_id)
        text = message.get('text', '').strip()

        # Обновляем имя пользователя на случай, если оно изменилось
        if user_id not in self.user_balances:
            self.user_balances[user_id] = {'name': user_name, 'balance': 0}
            self._save_user_balances()
        else:
            self.user_balances[user_id]['name'] = user_name
            self._save_user_balances()

        # --- Обработка команд (высокий приоритет) ---
        if text == "/start":
            self.pg_awaiting_input_type.pop(user_id, None) # Сброс состояния ожидания
            self._send_welcome_message(chat_id, user_id, user_name)
            return
        elif text == "/balance":
            self.pg_awaiting_input_type.pop(user_id, None) # Сброс состояния ожидания
            self._send_balance_message(chat_id, user_id)
            return
        elif text == "/leaderboard":
            self.pg_awaiting_input_type.pop(user_id, None) # Сброс состояния ожидания
            self._send_leaderboard_message(chat_id)
            return
        elif text == "/slots":
            self.pg_awaiting_input_type[user_id] = "slots_bet"
            self._send_bot_request('sendMessage', {
                'chat_id': chat_id,
                'text': "Отправьте вашу ставку ⭐ для игры в слоты (целое число).",
                'reply_markup': json.dumps({'inline_keyboard': [[{'text': "Отмена", 'callback_data': "cancel_custom_amount"}]]})
            })
            return

        # --- Обработка ввода пользовательской суммы доната или ставки (после команд) ---
        if user_id in self.pg_awaiting_input_type:
            input_type = self.pg_awaiting_input_type[user_id]
            try:
                amount = int(text)
                if amount <= 0:
                    self._send_bot_request('sendMessage', {
                        'chat_id': chat_id,
                        'text': "Сумма должна быть положительным числом. Пожалуйста, попробуйте снова или используйте кнопки.",
                        'reply_markup': json.dumps(self._get_welcome_keyboard(user_id))
                    })
                elif input_type == "donate":
                    self._initiate_star_payment(chat_id, user_id, amount, user_name)
                elif input_type == "slots_bet":
                    self._play_slots(chat_id, user_id, amount)
                
                self.pg_awaiting_input_type.pop(user_id, None) # Сброс состояния после успешного ввода
                return # Важно: завершить обработку
            except ValueError:
                self._send_bot_request('sendMessage', {
                    'chat_id': chat_id,
                    'text': "Неверный формат. Пожалуйста, введите число или используйте кнопки.",
                    'reply_markup': json.dumps(self._get_welcome_keyboard(user_id))
                })
                self.pg_awaiting_input_type.pop(user_id, None) # Сброс состояния после неверного ввода
                return # Важно: завершить обработку
        # --- Конец обработки ввода пользовательской суммы ---

        # Запасной вариант для неизвестных сообщений
        self._send_default_response(chat_id)


    def _handle_callback_query(self, query: dict):
        query_id = query['id']
        user = query['from']
        data = query.get('data')
        chat_id = query['message']['chat']['id'] # chat_id for `sendInvoice`
        user_id = str(user['id'])
        user_name = user.get('first_name', user_id)

        # Сброс ожидающего ввода, если пользователь нажимает любую кнопку, не связанную с вводом
        self.pg_awaiting_input_type.pop(user_id, None)

        if data == "donate_stars_custom": # Callback data для кнопки "Задонатить звезды"
            self.pg_awaiting_input_type[user_id] = "donate"
            self._send_bot_request('sendMessage', {
                'chat_id': chat_id,
                'text': "Отправьте желаемое количество звезд ⭐ в следующем сообщении. Например: `150`",
                'reply_markup': json.dumps({'inline_keyboard': [[{'text': "Отмена", 'callback_data': "cancel_custom_amount"}]]})
            })
            self._answer_callback_query(query_id) # Отвечаем на callback_query, чтобы убрать индикатор загрузки
        elif data == "cancel_custom_amount":
            self.pg_awaiting_input_type.pop(user_id, None)
            self._send_bot_request('sendMessage', {
                'chat_id': chat_id,
                'text': "Ввод суммы отменен. Вы можете выбрать другую опцию или начать заново /start.",
                'reply_markup': json.dumps(self._get_welcome_keyboard(user_id))
            })
            self._answer_callback_query(query_id)
        elif data == "show_leaderboard":
            self._send_leaderboard_message(chat_id)
            self._answer_callback_query(query_id)
        elif data == "play_slots":
            self.pg_awaiting_input_type[user_id] = "slots_bet"
            self._send_bot_request('sendMessage', {
                'chat_id': chat_id,
                'text': "Отправьте вашу ставку ⭐ для игры в слоты (целое число).",
                'reply_markup': json.dumps({'inline_keyboard': [[{'text': "Отмена", 'callback_data': "cancel_custom_amount"}]]})
            })
            self._answer_callback_query(query_id)
        else:
            self._answer_callback_query(query_id, "Неизвестная команда.", True)

    def _handle_pre_checkout_query(self, query: dict):
        query_id = query['id']
        user_id = str(query['from']['id'])
        user_name = query['from'].get('first_name', user_id)
        payload = query['invoice_payload']
        currency = query['currency']
        total_amount = query['total_amount'] # Сумма звезд

        # *** ВНИМАНИЕ: ЭТО ПОТЕНЦИАЛЬНО НЕБЕЗОПАСНАЯ ЛОГИКА! ***
        # Звезды начисляются на баланс пользователя СРАЗУ после pre_checkout_query.
        # Если пользователь отменит платеж после этой проверки, звезды останутся начисленными.
        # В реальных проектах НАСТОЯТЕЛЬНО рекомендуется добавлять звезды ТОЛЬКО после
        # получения сообщения successful_payment.

        log(f"[StarDonationAndSlotsBot - НЕБЕЗОПАСНО] Получен pre_checkout_query от {user_name} ({user_id}) на {total_amount} ⭐. PAYLOAD: {payload}")

        if currency != "XTR":
            self._answer_pre_checkout_query(query_id, False, "Неверная валюта. Используйте звезды Telegram.")
            return

        if not payload.startswith("donation_payload_"):
            self._answer_pre_checkout_query(query_id, False, "Неверный запрос на оплату.")
            return
        
        # Начисление звезд на баланс
        donated_stars = total_amount
        if user_id not in self.user_balances:
            self.user_balances[user_id] = {'name': user_name, 'balance': 0}
        
        self.user_balances[user_id]['balance'] += donated_stars
        self.user_balances[user_id]['name'] = user_name # Обновляем имя
        self._save_user_balances()
        
        log(f"[StarDonationAndSlotsBot - НЕБЕЗОПАСНО] Баланс пользователя {user_name} ({user_id}) ОБНОВЛЕН на {donated_stars} ⭐. Новый баланс: {self.user_balances[user_id]['balance']} ⭐")
        self.pg_processing_payment_payload[query_id] = {'user_id': user_id, 'stars_amount': donated_stars} # Сохраняем для successful_payment
        
        self._answer_pre_checkout_query(query_id, True)


    def _handle_successful_payment(self, message: dict):
        # *** ВНИМАНИЕ: ЭТО ПОТЕНЦИАЛЬНО НЕБЕЗОПАСНАЯ ЛОГИКА! ***
        # Поскольку звезды уже были начислены после pre_checkout_query,
        # здесь мы ТОЛЬКО подтверждаем успешность платежа и информируем пользователя.
        # Повторное начисление звезд ЗДЕСЬ приведет к двойному учету.

        user_id = str(message['from']['id'])
        user_name = message['from'].get('first_name', user_id)
        successful_payment = message['successful_payment']
        
        donated_stars = successful_payment['total_amount'] 
        payload = successful_payment['invoice_payload']

        log(f"[StarDonationAndSlotsBot - НЕБЕЗОПАСНО] Получен successful_payment от {user_name} ({user_id}), {donated_stars} ⭐. PAYLOAD: {payload}. Звезды уже должны быть начислены.")

        # Мы не добавляем звезды на баланс ЗДЕСЬ, т.к. это было сделано в _handle_pre_checkout_query.
        # Просто подтверждаем операцию.
        
        current_balance = self.user_balances.get(user_id, {'balance': 0})['balance']

        self._send_bot_request('sendMessage', {
            'chat_id': message['chat']['id'],
            'text': f"🎉 Оплата прошла успешно, {user_name}! Вы пожертвовали {donated_stars} ⭐. Ваш текущий баланс: {current_balance} ⭐",
            'reply_markup': json.dumps(self._get_welcome_keyboard(user_id))
        })

        if message['pre_checkout_query_id'] in self.pg_processing_payment_payload:
            del self.pg_processing_payment_payload[message['pre_checkout_query_id']]
        self._refresh_settings_page() # Обновить UI плагина для хоста

    # --- Функции для слотов ---

    def _play_slots(self, chat_id: int, user_id: str, bet_amount: int):
        user_balance = self.user_balances.get(user_id, {'balance': 0})['balance']
        user_name = self.user_balances.get(user_id, {'name': user_id})['name']

        if user_balance < bet_amount:
            self._send_bot_request('sendMessage', {
                'chat_id': chat_id,
                'text': f"У вас недостаточно звезд ⭐ для такой ставки ({bet_amount}). Ваш баланс: {user_balance} ⭐. Пополните его через донаты!",
                'reply_markup': json.dumps(self._get_welcome_keyboard(user_id))
            })
            return

        # Имитация прокрутки барабанов
        results = random.choices(self.SLOT_SYMBOLS, k=3)
        slot_display = " ".join(results)

        # Определение выигрыша
        if results[0] == results[1] == results[2]:
            winnings = bet_amount * 2
            user_balance += bet_amount # Удвоение ставки: (баланс - ставка) + (ставка * 2) = баланс + ставка
            result_text = f"🎊 {slot_display} 🎉\nПОБЕДА! Вы выиграли {winnings} ⭐. Ваш баланс удвоен!"
        elif results[0] == results[1] or results[0] == results[2] or results[1] == results[2]:
            winnings = bet_amount
            # user_balance не меняется, так как (баланс - ставка) + (ставка) = баланс
            result_text = f"🤝 {slot_display} 🤝\nНИЧЬЯ! Ваша ставка {bet_amount} ⭐ возвращена."
        else:
            winnings = 0
            user_balance -= bet_amount
            result_text = f"💔 {slot_display} 💔\nПРОИГРЫШ! Вы потеряли {bet_amount} ⭐."

        self.user_balances[user_id]['balance'] = user_balance
        self._save_user_balances()

        self._send_bot_request('sendMessage', {
            'chat_id': chat_id,
            'text': f"{user_name}, вы поставили {bet_amount} ⭐.\n\n{result_text}\n\nВаш новый баланс: {user_balance} ⭐",
            'reply_markup': json.dumps(self._get_welcome_keyboard(user_id))
        })

    # --- Вспомогательные функции бота ---

    def _send_welcome_message(self, chat_id: int, user_id: str, user_name: str):
        balance = self.user_balances.get(user_id, {'balance': 0})['balance']
        text = f"Привет, {user_name}! Ваш текущий баланс донатов: {balance} ⭐"
        self._send_bot_request('sendMessage', {
            'chat_id': chat_id,
            'text': text,
            'reply_markup': json.dumps(self._get_welcome_keyboard(user_id))
        })

    def _send_balance_message(self, chat_id: int, user_id: str):
        balance = self.user_balances.get(user_id, {'balance': 0})['balance']
        text = f"Ваш текущий баланс донатов: {balance} ⭐"
        self._send_bot_request('sendMessage', {
            'chat_id': chat_id,
            'text': text,
            'reply_markup': json.dumps(self._get_welcome_keyboard(user_id))
        })

    def _send_leaderboard_message(self, chat_id: int):
        self._send_bot_request('sendMessage', {
            'chat_id': chat_id,
            'text': self._format_leaderboard(),
            'reply_markup': json.dumps(self._get_welcome_keyboard(None)) # Для любого пользователя
        })

    def _send_default_response(self, chat_id: int):
        self._send_bot_request('sendMessage', {
            'chat_id': chat_id,
            'text': "Неизвестная команда. Используйте /start, /balance, /leaderboard или /slots.",
            'reply_markup': json.dumps(self._get_welcome_keyboard(None))
        })
    
    def _get_welcome_keyboard(self, user_id: Optional[str]) -> dict:
        # Теперь только кнопка "Задонатить звезды"
        donate_buttons_row = [{'text': "Задонатить звезды ⭐", 'callback_data': "donate_stars_custom"}] 
        
        keyboard = [
            donate_buttons_row, 
            [{'text': "Слоты 🎰", 'callback_data': "play_slots"}, {'text': "Таблица лидеров 🏆", 'callback_data': "show_leaderboard"}]
        ]
        return {'inline_keyboard': keyboard}

    def _initiate_star_payment(self, chat_id: int, user_id: str, stars_amount: int, user_name: str):
        # Используем Bot API sendInvoice для Star платежей.
        # provider_token не нужен для XTR.
        payload = f"donation_payload_{user_id}_{stars_amount}_{int(time.time())}"
        
        # prices: [{label: ..., amount: ...}] amount указывается в звездах
        prices = json.dumps([{'label': f"Донат {stars_amount} звезд", 'amount': stars_amount}])

        params = {
            'chat_id': chat_id,
            'title': 'Босс фейк оплаты Аугрэм', # Измененный заголовок платежа
            'description': f"Пожертвовать {stars_amount} ⭐",
            'payload': payload,
            'currency': 'XTR', # Валюта Telegram Stars
            'prices': prices,
            'start_parameter': 'donate' # Для deep link
            # 'provider_token': 'YOUR_PROVIDER_TOKEN' # Не нужен для XTR
        }
        self._send_bot_request('sendInvoice', params)

    # --- Управление данными и UI ---

    def _load_user_balances(self):
        try:
            pg_loaded_data = self.get_setting("star_donation_balances_pg", "{}")
            self.user_balances = json.loads(pg_loaded_data)
        except json.JSONDecodeError:
            self.user_balances = {}
        except Exception as e:
            log(f"[StarDonationAndSlotsBot] Ошибка загрузки балансов: {e}")
            self.user_balances = {}

    def _save_user_balances(self):
        self.set_setting("star_donation_balances_pg", json.dumps(self.user_balances))
        self._refresh_settings_page()

    def _format_leaderboard(self) -> str:
        if not self.user_balances:
            return "🏆 Таблица лидеров пуста.\nНикто еще не пожертвовал звезды."

        # Сортируем по балансу в убывающем порядке
        sorted_board = sorted(
            self.user_balances.items(),
            key=lambda item: item[1]['balance'],
            reverse=True
        )
        
        # Формируем текст без Markdown
        text_lines = ["🏆 Таблица лидеров по донатам звезд ⭐", ""]
        for i, (user_id, data) in enumerate(sorted_board[:15]): # Ограничим топ-15
            # Используем str() для user_id, чтобы избежать проблем с форматированием
            text_lines.append(f"{i+1}. {data['name']} - {data['balance']} ⭐")
        
        return "\n".join(text_lines)

    def _reset_all_balances(self, view: Optional[Any]):
        # Показываем подтверждение перед сбросом всех данных
        fragment = get_last_fragment()
        activity = fragment.getParentActivity() if fragment else None
        
        if not activity:
            run_on_ui_thread(lambda: BulletinHelper.show_error("Не удалось сбросить балансы: нет активного фрагмента.", get_last_fragment()))
            return

        builder = AlertDialogBuilder(activity)
        builder.set_title("Подтверждение сброса")
        builder.set_message("Вы уверены, что хотите сбросить балансы ВСЕХ пользователей? Это действие необратимо.")
        builder.set_positive_button("Сбросить", OnClickListener(lambda dialog_view: self._perform_reset_all_balances(dialog_view)))
        builder.set_negative_button("Отмена", OnClickListener(lambda dialog_view: dialog_view.dismiss()))
        builder.show()

    def _perform_reset_all_balances(self, dialog_view: Optional[Any]):
        self.user_balances = {}
        self.pg_awaiting_input_type = {} # Сброс всех ожиданий
        self._save_user_balances()
        if dialog_view: dialog_view.dismiss()
        run_on_ui_thread(lambda: BulletinHelper.show_success("Все балансы донатов сброшены!", get_last_fragment()))
        self._refresh_settings_page()

    def _refresh_settings_page(self):
        fragment = get_last_fragment()
        if fragment and hasattr(fragment, "rebuildAllFragments"):
            run_on_ui_thread(lambda: fragment.rebuildAllFragments(True))

    # --- Утилиты для Bot API ---

    def _send_bot_request(self, method: str, params: dict):
        if not self.bot_token: return
        url = f"https://api.telegram.org/bot{self.bot_token}/{method}"
        try:
            r = requests.post(url, json=params, timeout=10) # Используем json=params для более сложных объектов (цены, клавиатура)
            if r.status_code != 200: log(f"[StarDonationAndSlotsBot] API Error ({method}): {r.status_code} - {r.text}")
        except Exception as e:
            log(f"[StarDonationAndSlotsBot] Request failed ({method}): {e}")

    def _answer_callback_query(self, query_id: str, text: Optional[str] = None, show_alert: bool = False):
        params = {'callback_query_id': query_id}
        if text: params.update({'text': text, 'show_alert': show_alert})
        self._send_bot_request('answerCallbackQuery', params)

    def _answer_pre_checkout_query(self, query_id: str, ok: bool, error_message: Optional[str] = None):
        params = {'pre_checkout_query_id': query_id, 'ok': ok}
        if not ok and error_message:
            params['error_message'] = error_message
        self._send_bot_request('answerPreCheckoutQuery', params)
