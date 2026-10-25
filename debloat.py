#!/usr/bin/env python3
"""
TV Дом: проверка телевизора на лишние приложения.

Смотрит, что установлено на Android TV / Google TV, и предлагает отключить то, что безопасно: рекламу, демо-режимы
магазина, телеметрию, устаревшие сервисы. У каждого пункта есть описание и уровень риска. Ничего не удаляется:
приложения только отключаются (pm disable-user), и всё возвращается одной командой --restore.

Нужны Python 3 и adb (Android platform-tools), а на телевизоре включённая отладка по USB/сети.

    python3 debloat.py 192.168.1.50            # проверить и выбрать, что отключить
    python3 debloat.py 192.168.1.50 --report   # только показать, ничего не менять
    python3 debloat.py 192.168.1.50 --restore  # включить обратно всё, что отключал этот скрипт
    python3 debloat.py 192.168.1.50 --all-unknown   # показать и незнакомые системные приложения (не трогая их)

Никогда не предлагает: изображение, звук, входы HDMI и тюнер, пульт и Bluetooth, обновления, Google Play и сервисы
Google, заводские меню, части самого Android, а также приложения, которые сохранили сеть Wi-Fi (Android удалит сеть)
или нужны входам телевизора (проверяется по разрешениям).
"""
import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys

# Уровни: safe = реклама, демо, телеметрия, устаревшее: рекомендуется; optional = можно, если не пользуетесь.
SAFE, OPTIONAL = "safe", "optional"

DB = {
    # --- Google
    "com.google.android.play.games": (OPTIONAL, "Google Play Игры", "Каталог игр Google. Сами игры работают и без него."),
    "com.google.android.youtube.tvmusic": (OPTIONAL, "YouTube Music", "Музыка YouTube. Обычный YouTube не затрагивается."),
    "com.google.android.videos": (SAFE, "Google Play Фильмы", "Сервис закрыт, приложение больше ничего не показывает."),
    "com.google.android.feedback": (SAFE, "Отзывы Google", "Отправка отчётов и отзывов в Google."),
    "com.android.printspooler": (SAFE, "Печать", "Служба печати: на телевизоре не нужна."),
    "com.android.dreams.basic": (OPTIONAL, "Простая заставка", "Стандартная заставка Android. У TV Дом есть своя."),
    "com.android.dreams.phototable": (OPTIONAL, "Заставка «Фотостол»", "Старая заставка Android."),
    "com.google.android.backdrop": (OPTIONAL, "Фоновый режим Google", "Заставка Google с фото. У TV Дом есть своя."),
    "com.google.android.apps.tv.dreamx": (OPTIONAL, "Фоновый режим Google TV", "Заставка Google TV. У TV Дом есть своя."),
    "com.google.android.tvrecommendations": (OPTIONAL, "Рекомендации Android TV", "Каналы рекомендаций стандартного лаунчера."),
    "com.google.android.leanbacklauncher.recommendations": (OPTIONAL, "Рекомендации лаунчера", "Рекомендации старого лаунчера Android TV."),
    "com.google.android.katniss": (OPTIONAL, "Google Ассистент", "Голосовой поиск. Если отключить, кнопка микрофона перестанет работать."),
    "com.android.hotwordenrollment.okgoogle": (OPTIONAL, "«Окей, Google»", "Распознавание фразы «Окей, Google»."),
    "com.google.android.marvin.talkback": (OPTIONAL, "TalkBack", "Озвучивание экрана для незрячих. Не отключайте, если пользуетесь."),
    # --- Стриминг, который в России не работает или не нужен
    "com.netflix.ninja": (OPTIONAL, "Netflix", "Netflix в России недоступен."),
    "com.amazon.amazonvideo.livingroom": (OPTIONAL, "Prime Video", "Prime Video в России недоступен."),
    "com.disney.disneyplus": (OPTIONAL, "Disney+", "Disney+ в России недоступен."),
    "com.wbd.stream": (OPTIONAL, "Max", "Max (HBO) в России недоступен."),
    "com.hulu.livingroomplus": (OPTIONAL, "Hulu", "Hulu работает только в США."),
    "com.peacocktv.peacockandroid": (OPTIONAL, "Peacock", "Peacock работает только в США."),
    "com.google.android.apps.youtube.kids": (OPTIONAL, "YouTube Детям", "Детский YouTube."),
    # --- TCL
    "com.tcl.magiconnectfree": (OPTIONAL, "TCL MagiConnect", "Управление с телефона и показ экрана от TCL."),
    "com.tcl.miracast": (OPTIONAL, "TCL Miracast", "Показ экрана телефона по Miracast (Chromecast работает и без него)."),
    "com.tcl.waterfall.overseas": (SAFE, "TCL Channel", "Рекламные каналы TCL."),
    "com.tcl.channelplus": (SAFE, "TCL Channel+", "Рекламное «живое ТВ» TCL."),
    "com.tcl.messagebox": (SAFE, "Сообщения TCL", "Рекламные уведомления TCL."),
    "com.tcl.usercenter": (OPTIONAL, "Аккаунт TCL", "Учётная запись TCL."),
    "com.tcl.exhibit": (SAFE, "Демо TCL", "Демонстрационный режим для витрины магазина."),
    "com.tcl.esticker": (SAFE, "Наклейки TCL", "Рекламные наклейки на экране в магазине."),
    "com.tcl.hotelmenu": (SAFE, "Гостиничное меню TCL", "Режим для гостиниц."),
    "com.tcl.repairguide": (SAFE, "Руководство по ремонту TCL", "Справка для сервиса."),
    "com.tcl.ocean.instructions": (SAFE, "Инструкция TCL", "Электронное руководство."),
    "com.tcl.logkit": (SAFE, "Журналы TCL", "Сбор журналов и отправка производителю."),
    # --- Xiaomi
    "com.miui.tv.analytics": (SAFE, "Аналитика Xiaomi", "Телеметрия: отправка статистики использования."),
    "com.xiaomi.statistic": (SAFE, "Статистика Xiaomi", "Телеметрия: отправка статистики использования."),
    "com.mitv.tvhome.mitvplus": (SAFE, "Mi TV+", "Рекламные каналы Xiaomi."),
    "com.xm.webcontent": (SAFE, "Веб-контент Xiaomi", "Рекламные материалы Xiaomi."),
    "com.xiaomi.mitv.manualhelp": (SAFE, "Инструкция Xiaomi", "Электронное руководство."),
    # --- Skyworth, KTC, Multilaser, Philco и другие на MediaTek
    "com.smartdevice.epopplayer": (SAFE, "Демо Skyworth", "Демонстрационный ролик для магазина (запускается сам)."),
    "com.coocaa.demonow.lightining": (SAFE, "Демо Coocaa", "Демонстрационный ролик для магазина (запускается сам)."),
    "com.smartdevice.recommendation": (SAFE, "Рекомендации Coolita", "Рекламные каналы."),
    "com.Smartdevice.emanual": (SAFE, "Инструкция Skyworth", "Электронное руководство."),
    "tv.anoki.acr.controller": (SAFE, "Anoki ACR", "Распознаёт, что показывает экран, для рекламы (слежка)."),
    "tv.anoki.acr.anokiacroptin": (SAFE, "Anoki ACR", "Распознаёт, что показывает экран, для рекламы (слежка)."),
    "com.mediatek.tv.retaildemo": (SAFE, "Демо MediaTek", "Демонстрационный режим для магазина."),
    "com.electronic.manual": (SAFE, "Инструкция", "Электронное руководство."),
    "com.expressluck.tvhelpmanual": (SAFE, "Инструкция", "Электронное руководство."),
    "com.homwee.aipont": (SAFE, "AI PONT", "Рекламные каналы и загрузка приложений."),
    "com.homwee.help": (SAFE, "Инструкция", "Электронное руководство."),
    # fusion.android.tv.demo на части плат MediaTek принимает кнопку Input: скрипт проверит разрешения перед отключением
    "fusion.android.tv.demo": (SAFE, "Демо-режим", "Демонстрационный режим для магазина."),
}

# Не предлагаются никогда, даже если похожи на рекламу по имени.
PROTECTED = {
    "com.google.android.gms", "com.google.android.gsf", "com.android.vending", "com.google.android.apps.mediashell",
    "com.google.android.tv.remote.service", "com.google.android.inputmethod.latin", "com.google.android.webview",
    "com.android.tv.settings", "com.android.systemui", "android", "ru.tvdom",
    "com.google.android.tvlauncher", "com.google.android.apps.tv.launcherx", "com.google.android.leanbacklauncher",
    "com.google.android.tungsten.setupwraith", "com.google.android.partnersetup", "com.android.providers.tv",
    "com.google.android.tv.googletvremotepairer", "com.mitv.tvhome.atv", "com.tcl.tv", "com.tcl.tvinput",
    "com.dolby.android.audio.service", "com.mediatek.tv.service", "com.mediatek.tvinput", "com.mediatek.wwtv.tvcenter",
}
CORE = re.compile(r"android|com\.android\.(systemui|shell|phone|settings|tv\.settings)"
                  r"|com\.(google\.)?android\.(permissioncontroller|packageinstaller|ext\.services|ext\.shared|modulemetadata)"
                  r"|com\.google\.android\.overlay\.modules.*|com\.android\.providers\..*")
FACTORY = re.compile(r"(.*\.)?(factory|factorymenu|facmenu|fac|hkcfac|testpq)(\..*)?")
VITAL = re.compile(r"(bluetooth|bt\.|autopair|remote|rcu|keyhelp|hotkey|globalkey|input|tuner|livetv|tvcenter|hdmi|cec"
                   r"|audio|sound|dolby|picture|pq|display|setup|wizard|update|ota|upgrade|settings|launcher|webview"
                   r"|keyboard|inputmethod|ime|provider|system|overlay|wifi|network|cast|miracast)", re.I)
# Похоже на рекламу или слежку по имени: показывается отдельно, никогда не выбирается автоматически.
SUSPECT = re.compile(r"(\.ads?\b|\.ad\.|advert|analytics|statistic|telemetry|tracker|retaildemo|exhibit|\.demo\b|promo)", re.I)


class Adb:
    def __init__(self, target):
        self.target = target

    def run(self, *args, check=False):
        cmd = ["adb"] + (["-s", self.target] if self.target else []) + list(args)
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if check and r.returncode != 0:
            sys.exit(f"adb: {(r.stderr or r.stdout).strip()}")
        return r.stdout

    def shell(self, command):
        return self.run("shell", command)


def connect(ip):
    if shutil.which("adb") is None:
        sys.exit("Не найден adb. Установите Android platform-tools: https://developer.android.com/tools/releases/platform-tools")
    if not ip:
        return Adb(None)
    target = ip if ":" in ip else ip + ":5555"
    out = subprocess.run(["adb", "connect", target], capture_output=True, text=True).stdout
    if "connected" not in out:
        sys.exit(f"Не удалось подключиться к {target}: {out.strip()}\n"
                 "Проверьте, что на телевизоре включена отладка по USB (Настройки → Для разработчиков) и что ТВ в той же сети.")
    adb = Adb(target)
    if "device" not in adb.run("get-state"):
        sys.exit("Подтвердите на телевизоре «Разрешить отладку по USB?» (отметьте «Всегда разрешать») и запустите снова.")
    return adb


def packages(adb, flag):
    return {l.split(":", 1)[1].strip() for l in adb.shell(f"pm list packages {flag}").splitlines() if l.startswith("package:")}


def wifi_owners(adb):
    """Приложения, сохранившие сети Wi-Fi: если их отключить, Android удалит эти сети."""
    out = adb.shell("dumpsys wifi")
    return set(re.findall(r"(?:creatorName|cname|creator)=([\w.]+)", out))


def needed_by_tv(adb, pkgs):
    """Из кандидатов: нужные входам ТВ (TV_INPUT_HARDWARE) и пульту (кнопка Input, сопряжение)."""
    keep = set()
    for p in sorted(pkgs):
        d = adb.shell(f"dumpsys package {p}")
        if re.search(r"TV_INPUT_HARDWARE|TV_INPUT_BUTTON|CONNECT_INPUT|AddAccessory", d):
            keep.add(p)
    return keep


def allowed(pkg):
    return pkg not in PROTECTED and not pkg.startswith("com.amazon.") and not CORE.fullmatch(pkg) and not FACTORY.fullmatch(pkg)


def state_file(adb):
    serial = re.sub(r"[^\w.-]", "_", adb.shell("getprop ro.serialno").strip() or (adb.target or "tv"))
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), f"tvdom-disabled-{serial}.json")


def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"disabled": []}


def save_state(path, state):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def ask(prompt):
    try:
        return input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)


def parse_choice(text, n):
    picked = set()
    for part in re.split(r"[,\s]+", text):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)-(\d+)", part)
        if m:
            picked.update(range(int(m.group(1)), int(m.group(2)) + 1))
        elif part.isdigit():
            picked.add(int(part))
    return sorted(i for i in picked if 1 <= i <= n)


def restore(adb):
    path = state_file(adb)
    state = load_state(path)
    if not state["disabled"]:
        print("Этот скрипт ничего не отключал на этом телевизоре.")
        return
    for pkg in state["disabled"]:
        out = adb.shell(f"pm enable {pkg}").strip()
        print(f"  {pkg}: {out or 'ok'}")
    state["disabled"] = []
    save_state(path, state)
    print("Готово: всё включено обратно.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ip", nargs="?", help="IP-адрес телевизора (без него — подключённое по USB устройство)")
    ap.add_argument("--report", action="store_true", help="только показать, ничего не менять")
    ap.add_argument("--restore", action="store_true", help="включить обратно всё, что отключал этот скрипт")
    ap.add_argument("--all-unknown", action="store_true", help="перечислить и незнакомые системные приложения")
    a = ap.parse_args()

    adb = connect(a.ip)
    if a.restore:
        restore(adb)
        return
    brand = adb.shell("getprop ro.product.brand").strip()
    model = adb.shell("getprop ro.product.model").strip()
    android = adb.shell("getprop ro.build.version.release").strip()
    print(f"\nТелевизор: {brand} {model}, Android {android}")

    system, user, disabled = packages(adb, "-s"), packages(adb, "-3"), packages(adb, "-d")
    installed = system | user
    enabled = installed - disabled
    print(f"Приложений: {len(installed)} (системных {len(system)}, установленных вами {len(user)}, отключено {len(disabled)})")

    known = [p for p in sorted(enabled) if p in DB and allowed(p)]
    suspects = [p for p in sorted(enabled & system) if p not in DB and allowed(p) and SUSPECT.search(p) and not VITAL.search(p)]
    print("Проверяю, что нужно Wi-Fi, входам и пульту…")
    wifi = wifi_owners(adb)
    vital = needed_by_tv(adb, set(known) | set(suspects))
    skipped = [(p, "сохранило сеть Wi-Fi") for p in known + suspects if p in wifi] + \
              [(p, "нужно входам или пульту") for p in known + suspects if p in vital and p not in wifi]
    skip = {p for p, _ in skipped}
    known = [p for p in known if p not in skip]
    suspects = [p for p in suspects if p not in skip]

    rows = []
    for level, title in ((SAFE, "Можно смело отключить (реклама, демо, телеметрия, устаревшее)"),
                         (OPTIONAL, "Можно отключить, если не пользуетесь")):
        group = [p for p in known if DB[p][0] == level]
        if not group:
            continue
        print(f"\n{title}:")
        for p in group:
            rows.append(p)
            _, name, desc = DB[p]
            print(f"  {len(rows):2}. {name}  [{p}]\n      {desc}")
    if suspects:
        print("\nПохоже на рекламу или слежку по названию (не проверено, решайте сами):")
        for p in suspects:
            rows.append(p)
            print(f"  {len(rows):2}. {p}\n      Незнакомое системное приложение; по названию похоже на рекламу или аналитику.")
    if skipped:
        print("\nНе предлагаю, хотя в списке:")
        for p, why in skipped:
            print(f"   - {p}: {why}")
    already = [p for p in sorted(disabled) if p in DB]
    if already:
        print("\nУже отключено: " + ", ".join(DB[p][1] for p in already))
    if a.all_unknown:
        unknown = sorted(p for p in enabled & system if p not in DB and p not in suspects)
        print(f"\nНезнакомые системные приложения ({len(unknown)}), их лучше не трогать:")
        for p in unknown:
            print("   " + p)

    if not rows:
        print("\nНечего отключать: лишних приложений из списка не найдено.")
        return
    if a.report:
        print("\n(--report: ничего не изменено)")
        return

    safe_n = [i + 1 for i, p in enumerate(rows) if p in DB and DB[p][0] == SAFE]
    print("\nЧто отключить? Номера через запятую или диапазон (1,3,5-7)"
          + (f", «s» — все безопасные ({len(safe_n)})" if safe_n else "") + ", Enter — ничего.")
    answer = ask("> ")
    picked = safe_n if answer == "s" else parse_choice(answer, len(rows))
    if not picked:
        print("Ничего не изменено.")
        return
    chosen = [rows[i - 1] for i in picked]
    print("\nБудут отключены (не удалены):")
    for p in chosen:
        print(f"   - {DB[p][1] if p in DB else p}  [{p}]")
    if ask("Продолжить? [y/N] ") not in ("y", "yes", "д", "да"):
        print("Ничего не изменено.")
        return

    path = state_file(adb)
    state = load_state(path)
    for p in chosen:
        out = adb.shell(f"pm disable-user --user 0 {p}").strip()
        ok = "disabled" in out
        print(f"   {'✓' if ok else '✗'} {p}: {out}")
        if ok and p not in state["disabled"]:
            state["disabled"].append(p)
    state["at"] = datetime.datetime.now().isoformat(timespec="seconds")
    save_state(path, state)
    print(f"\nГотово. Список отключённого: {path}\nВернуть всё: python3 {os.path.basename(__file__)} {a.ip or ''} --restore")


if __name__ == "__main__":
    main()
