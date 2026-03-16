__id__ = "pierce_hunter"
__name__ = "Pierce Hunter"
__description__ = "Searches public channels and chats by a large piercing-related keyword base and can send clickable links to a target bot."
__version__ = "1.0.0"
__author__ = "Codex"
__min_version__ = "11.12.0"

import os
import random
import re
import threading
import time
import urllib.request

from android_utils import log
from base_plugin import BasePlugin, HookResult, HookStrategy
from client_utils import RequestCallback, send_document, send_request, send_text
from org.telegram.tgnet import TLRPC
from ui.settings import Divider, Header, Input, Switch, Text


KEYWORDS_URL = "https://raw.githubusercontent.com/qrrrv/Plugin-sfanstat/refs/heads/main/pierce_hunter_keywords.txt"

# Manual code config:
# change this line if you want another target bot by default.
TARGET_BOT_USERNAME = "@Eskigrbot"
DEFAULT_TARGET = TARGET_BOT_USERNAME
SEARCH_PREFIXES = ["чат", "группа", "channel", "chat", "group", "समूह", "चैट"]
FALLBACK_KEYWORDS = [
    "пирсинг", "мастер пирсинга", "студия пирсинга", "септум", "хеликс", "трагус", "микродермал",
    "piercing", "body piercing", "piercing studio", "septum piercing", "helix piercing",
    "पियर्सिंग", "नाक पियर्सिंग", "कान पियर्सिंग", "पियर्सिंग स्टूडियो",
]


def safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def safe_text(value, default=""):
    text = str(value if value is not None else default)
    return text.strip()


class Plugin(BasePlugin):
    def __init__(self):
        super().__init__()
        self.keywords = []
        self.keywords_source = "builtin"
        self.keywords_updated_at = 0
        self.found_channels = {}
        self._callbacks = []
        self._farm_timer = None
        self._farm_running = False
        self._stats = {"searches": 0, "hits": 0, "last_query": "-", "last_send": "-"}

    def on_plugin_load(self):
        self.add_on_send_message_hook()
        self._load_keywords_async(force=False)

    def on_plugin_unload(self):
        self._farm_running = False
        if self._farm_timer:
            try:
                self._farm_timer.cancel()
            except Exception:
                pass
            self._farm_timer = None

    def create_settings(self):
        return [
            Header(text="Pierce Hunter"),
            Input(
                key="command_prefix",
                text="Префикс команд",
                default=".pierce",
                subtext="Например: .pierce или .hunt",
            ),
            Divider(),
            Header(text="Target Bot"),
            Input(
                key="farm_target",
                text="Куда отправлять ссылки",
                default=DEFAULT_TARGET,
                subtext="Можно @username бота или числовой peer id. Это можно менять прямо отсюда.",
            ),
            Text(
                text="Ручной target в коде",
                subtext=f'TARGET_BOT_USERNAME = "{TARGET_BOT_USERNAME}"\nНастройка выше имеет приоритет, если ты менял её в UI.',
            ),
            Divider(),
            Header(text="Поиск и фарм"),
            Input(
                key="results_per_message",
                text="Ссылок за раз",
                default="8",
                subtext="Сколько ссылок отправлять в одном результате.",
            ),
            Input(
                key="max_results",
                text="Лимит поиска",
                default="30",
                subtext="Максимум результатов из Telegram API за запрос.",
            ),
            Input(
                key="farm_interval",
                text="Интервал автофарма (сек)",
                default="45",
                subtext="Через сколько секунд делать следующий рандомный прогон.",
            ),
            Switch(
                key="output_to_file",
                text="При поиске делать TXT файл",
                default=True,
                subtext="Отправляет в чат полный список результатов отдельным txt-файлом.",
            ),
            Divider(),
            Header(text="Команды"),
            Text(
                text="Команды",
                subtext=self._command_help_text(),
            ),
            Text(
                text="Статус базы слов",
                subtext=self._keywords_status_text(),
            ),
        ]

    def _plugin_dir(self):
        try:
            return os.path.dirname(os.path.abspath(__file__))
        except Exception:
            return "/data/user/0/com.exteragram.messenger/files/plugins"

    def _keywords_cache_path(self):
        return os.path.join(self._plugin_dir(), "pierce_hunter_keywords_cached.txt")

    def _keywords_status_text(self):
        stamp = self.keywords_updated_at or 0
        stamp_text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stamp)) if stamp else "not yet"
        return f"Источник: {self.keywords_source}\nСлов: {len(self.keywords)}\nОбновлено: {stamp_text}"

    def _command_prefix(self):
        prefix = safe_text(self.get_setting("command_prefix", ".pierce"), ".pierce")
        return prefix or ".pierce"

    def _command_help_text(self):
        prefix = self._command_prefix()
        return (
            f"{prefix} rand\n"
            f"{prefix} search <слово>\n"
            f"{prefix} target @bot\n"
            f"{prefix} farm start|stop\n"
            f"{prefix} stats\n"
            f"{prefix} reload"
        )

    def _load_keywords_async(self, force=False):
        def worker():
            self._load_keywords(force=force)

        thread = threading.Thread(target=worker, name="pierce_keywords_loader", daemon=True)
        thread.start()

    def _load_keywords(self, force=False):
        cache_path = self._keywords_cache_path()
        text = ""
        source = "builtin"
        if not force and os.path.exists(cache_path):
            try:
                text = open(cache_path, "r", encoding="utf-8").read()
                source = "cache"
            except Exception:
                text = ""
        if force or not text:
            try:
                request = urllib.request.Request(KEYWORDS_URL, headers={"User-Agent": "PierceHunter/1.0"})
                with urllib.request.urlopen(request, timeout=15) as response:
                    text = response.read().decode("utf-8")
                open(cache_path, "w", encoding="utf-8").write(text)
                source = "github"
            except Exception as e:
                log(f"[PierceHunter] keyword download error: {e}")
                if not text:
                    text = "\n".join(FALLBACK_KEYWORDS)
                    source = "builtin"
        merged = []
        seen = set()
        for line in text.splitlines() + FALLBACK_KEYWORDS:
            item = line.strip()
            if not item:
                continue
            key = item.casefold()
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
        self.keywords = merged
        self.keywords_source = source
        self.keywords_updated_at = int(time.time())

    def _request(self, req, handler):
        callback = None

        def wrapped(response, error):
            try:
                handler(response, error)
            finally:
                try:
                    self._callbacks.remove(callback)
                except Exception:
                    pass

        callback = RequestCallback(wrapped)
        self._callbacks.append(callback)
        return send_request(req, callback)

    def _normalize_channels(self, chats):
        unique = []
        seen = set()
        for chat in chats or []:
            username = safe_text(getattr(chat, "username", ""))
            if not username:
                continue
            key = username.casefold()
            if key in seen:
                continue
            seen.add(key)
            broadcast = bool(getattr(chat, "broadcast", False))
            megagroup = bool(getattr(chat, "megagroup", False))
            title = safe_text(getattr(chat, "title", "")) or f"@{username}"
            count = safe_int(getattr(chat, "participants_count", 0), 0)
            unique.append(
                {
                    "username": f"@{username}",
                    "title": title,
                    "members": count,
                    "verified": bool(getattr(chat, "verified", False)),
                    "link": f"https://t.me/{username}",
                    "type": "Канал" if broadcast and not megagroup else "Чат",
                }
            )
        return unique

    def _search_variants(self, query):
        variants = [query]
        variants.extend(f"{prefix} {query}" for prefix in SEARCH_PREFIXES)
        if " " in query:
            variants.append(query.split()[0])
        out = []
        seen = set()
        for item in variants:
            key = item.casefold().strip()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(item.strip())
        return out[:6]

    def _search_channels_async(self, query, limit, on_done):
        variants = self._search_variants(query)
        results = []
        seen = set()

        def consume(index):
            if index >= len(variants) or len(results) >= limit:
                on_done(results[:limit])
                return
            req = TLRPC.TL_contacts_search()
            req.q = variants[index]
            req.limit = limit

            def handle(response, error):
                if error:
                    log(f"[PierceHunter] search error for {variants[index]}: {getattr(error, 'text', error)}")
                else:
                    for item in self._normalize_channels(getattr(response, "chats", [])):
                        if item["username"] not in seen:
                            seen.add(item["username"])
                            results.append(item)
                if results:
                    on_done(results[:limit])
                else:
                    consume(index + 1)

            self._request(req, handle)

        consume(0)

    def _format_member_count(self, count):
        if count >= 1000000:
            return f"{count / 1000000:.1f}M"
        if count >= 1000:
            return f"{count / 1000:.1f}k"
        return str(count)

    def _make_result_text(self, query, channels, limit=None):
        selected = channels[: limit or len(channels)]
        lines = [f"🔎 Pierce Hunter: {query}", f"✅ Найдено: {len(channels)}", ""]
        for item in selected:
            icon = "📢" if item["type"] == "Канал" else "👥"
            lines.append(f"{icon} {item['title']} [{self._format_member_count(item['members'])}]")
            lines.append(item["link"])
            lines.append("")
        return "\n".join(lines).strip()

    def _write_results_file(self, query, channels):
        safe_query = re.sub(r'[^0-9A-Za-zА-Яа-я._-]+', '_', query).strip("_")[:48] or "search"
        path = os.path.join(self._plugin_dir(), f"pierce_hunter_{safe_query}.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"Query: {query}\n")
            handle.write(f"Results: {len(channels)}\n")
            handle.write("=" * 48 + "\n\n")
            for item in channels:
                handle.write(f"{item['title']}\n")
                handle.write(f"{item['type']} | {item['username']} | {item['members']}\n")
                handle.write(f"{item['link']}\n")
                handle.write("-" * 48 + "\n")
        return path

    def _resolve_and_send(self, target, text, report_peer=None):
        target = safe_text(target, DEFAULT_TARGET)
        if not target:
            if report_peer is not None:
                send_text(report_peer, "❌ Не задан target для отправки.")
            return
        if target.startswith("@"):
            req = TLRPC.TL_contacts_resolveUsername()
            req.username = target[1:]

            def handle(response, error):
                if error:
                    if report_peer is not None:
                        send_text(report_peer, f"❌ Не удалось резолвить {target}: {getattr(error, 'text', error)}")
                    return
                user_id = None
                users = getattr(response, "users", []) or []
                if users:
                    user_id = getattr(users[0], "id", None)
                if user_id is None:
                    if report_peer is not None:
                        send_text(report_peer, f"❌ Не найден peer для {target}")
                    return
                send_text(user_id, text)
                if report_peer is not None:
                    send_text(report_peer, f"✅ Отправлено в {target}")

            self._request(req, handle)
            return
        try:
            send_text(int(target), text)
            if report_peer is not None:
                send_text(report_peer, f"✅ Отправлено в {target}")
        except Exception as e:
            if report_peer is not None:
                send_text(report_peer, f"❌ Ошибка отправки в {target}: {e}")

    def _notify(self, peer, text):
        if peer is not None:
            send_text(peer, text)
        else:
            log(f"[PierceHunter] {text}")

    def _run_random_search(self, peer, send_to_target=False):
        if not self.keywords:
            self._load_keywords(force=False)
        if not self.keywords:
            self._notify(peer, "❌ База слов пока не загружена.")
            return
        query = None

        def try_next(remaining):
            nonlocal query
            if remaining <= 0:
                self._notify(peer, "❌ Не удалось найти каналы по случайным словам.")
                return
            query = random.choice(self.keywords)
            self._stats["last_query"] = query
            def handle_done(channels):
                if not channels:
                    try_next(remaining - 1)
                    return
                self._stats["searches"] += 1
                self._stats["hits"] += len(channels)
                text = self._make_result_text(query, channels, limit=safe_int(self.get_setting("results_per_message", "8"), 8))
                if send_to_target:
                    self._stats["last_send"] = self.get_setting("farm_target", DEFAULT_TARGET)
                    self._resolve_and_send(self.get_setting("farm_target", DEFAULT_TARGET), text, report_peer=peer)
                else:
                    self._notify(peer, text)
            self._search_channels_async(query, safe_int(self.get_setting("max_results", "30"), 30), handle_done)
        try_next(min(5, len(self.keywords)))

    def _run_search(self, peer, query):
        limit = safe_int(self.get_setting("max_results", "30"), 30)
        per_message = safe_int(self.get_setting("results_per_message", "8"), 8)
        self._stats["last_query"] = query
        send_text(peer, f"🔍 Ищу: {query}")

        def done(channels):
            if not channels:
                send_text(peer, f"❌ По запросу '{query}' ничего не найдено.")
                return
            self.found_channels[query] = channels
            self._stats["searches"] += 1
            self._stats["hits"] += len(channels)
            send_text(peer, self._make_result_text(query, channels, limit=per_message))
            if self.get_setting("output_to_file", True):
                try:
                    path = self._write_results_file(query, channels)
                    send_document(peer, path, caption=f"📄 Полный список по '{query}': {len(channels)} результатов")
                except Exception as e:
                    log(f"[PierceHunter] file export error: {e}")

        self._search_channels_async(query, limit, done)

    def _schedule_farm(self):
        if not self._farm_running:
            return
        interval = max(15, min(900, safe_int(self.get_setting("farm_interval", "45"), 45)))

        def tick():
            if not self._farm_running:
                return
            self._run_random_search(None, send_to_target=True)
            self._schedule_farm()

        self._farm_timer = threading.Timer(interval, tick)
        self._farm_timer.daemon = True
        self._farm_timer.start()

    def _handle_command(self, peer, message):
        parts = message.split(maxsplit=2)
        if len(parts) == 1 or parts[1] == "help":
            send_text(
                peer,
                "Pierce Hunter\n\n" + self._command_help_text(),
            )
            return
        action = parts[1].strip().lower()
        if action == "rand":
            self._run_random_search(peer, send_to_target=False)
        elif action == "search" and len(parts) >= 3:
            self._run_search(peer, parts[2].strip())
        elif action == "target" and len(parts) >= 3:
            target = parts[2].strip()
            self.set_setting("farm_target", target)
            send_text(peer, f"✅ Target обновлён: {target}")
        elif action == "stats":
            send_text(
                peer,
                f"📊 Pierce Hunter\n"
                f"Слов в базе: {len(self.keywords)}\n"
                f"Источник базы: {self.keywords_source}\n"
                f"Поисков: {self._stats['searches']}\n"
                f"Найдено ссылок: {self._stats['hits']}\n"
                f"Последний запрос: {self._stats['last_query']}\n"
                f"Последняя отправка: {self._stats['last_send']}",
            )
        elif action == "reload":
            self._load_keywords_async(force=True)
            send_text(peer, "🔄 Запустил обновление базы слов с GitHub.")
        elif action == "farm" and len(parts) >= 3:
            sub = parts[2].strip().lower()
            if sub == "start":
                self._farm_running = True
                self._schedule_farm()
                send_text(peer, f"🚀 Автофарм запущен. Target: {self.get_setting('farm_target', DEFAULT_TARGET)}")
            elif sub == "stop":
                self._farm_running = False
                if self._farm_timer:
                    try:
                        self._farm_timer.cancel()
                    except Exception:
                        pass
                    self._farm_timer = None
                send_text(peer, "🛑 Автофарм остановлен.")
            else:
                send_text(peer, "❌ Используй: .pierce farm start или .pierce farm stop")
        else:
            send_text(peer, f"❌ Команда не распознана. Напиши {self._command_prefix()} help")

    def on_send_message_hook(self, account, params):
        text = getattr(params, "message", None)
        if not isinstance(text, str):
            return HookResult()
        msg = text.strip()
        prefix = self._command_prefix().strip()
        if not prefix:
            prefix = ".pierce"
        if not msg.lower().startswith(prefix.lower()):
            return HookResult()
        peer = getattr(params, "peer", None)
        try:
            self._handle_command(peer, msg)
        except Exception as e:
            log(f"[PierceHunter] command error: {e}")
            if peer is not None:
                send_text(peer, f"❌ Ошибка Pierce Hunter: {e}")
        return HookResult(strategy=HookStrategy.CANCEL, params=params)
