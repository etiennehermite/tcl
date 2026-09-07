#!/usr/bin/env python3
"""
Surveillance des créneaux de tennis disponibles au club "Jardin du Luxembourg"
(Anybuddy) et notification Telegram dès qu'un NOUVEAU créneau correspondant
apparaît.

Critères surveillés :
- Semaine (lundi-vendredi) : créneaux à 8h00 ou 8h30 (matin), ou à partir de
  18h00 inclus (soir)
- Week-end (samedi-dimanche) : tout créneau, à n'importe quelle heure
#!/usr/bin/env python3
"""
Surveillance des créneaux de tennis disponibles au club "Jardin du Luxembourg"
(Anybuddy) et notification Telegram dès qu'un NOUVEAU créneau correspondant
apparaît.

Critères surveillés automatiquement :
- Semaine (lundi-vendredi) : créneaux à 8h00 ou 8h30 (matin), ou à partir de
  18h00 inclus (soir)
- Week-end (samedi-dimanche) : tout créneau, à n'importe quelle heure

Vérification manuelle à la demande : envoyer n'importe quel message au bot
Telegram déclenche, au prochain passage planifié (toutes les 10 min), une
réponse listant TOUTES les disponibilités des 7 prochains jours, sans filtre
horaire.

Variables d'environnement requises :
- TELEGRAM_BOT_TOKEN : token du bot Telegram (créé via @BotFather)
- TELEGRAM_CHAT_ID   : id du chat/utilisateur à notifier

État persistant : notified_slots.json, un objet {"notified_slots": [...],
"last_update_id": ...} qui retient les créneaux déjà signalés (pour ne
notifier que les nouveaux) et le dernier message Telegram déjà traité (pour
ne réagir qu'aux nouveaux messages).
"""

import datetime
import json
import os
import sys
from zoneinfo import ZoneInfo

import requests

CLUB_SLUG = "tennis-jardin-du-luxembourg"
ACTIVITY = "tennis"
LOOKAHEAD_DAYS = 7
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notified_slots.json")

PARIS = ZoneInfo("Europe/Paris")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def fetch_day(date_str):
    url = "https://www.anybuddyapp.com/api/v1/availabilities"
    params = {
        "clubSlug": CLUB_SLUG,
        "dateFrom": date_str,
        "dateTo": f"{date_str}T23:59",
        "activity": ACTIVITY,
    }
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, params=params, headers=headers, timeout=15)
    r.raise_for_status()
    return r.json().get("data", [])


def matches_criteria(dt):
    weekday = dt.weekday()  # 0=lundi ... 5=samedi, 6=dimanche
    if weekday >= 5:
        return True  # week-end : tout créneau
    if dt.hour == 8 and dt.minute in (0, 30):
        return True  # matin de semaine
    if dt.hour >= 18:
        return True  # soir de semaine
    return False


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            # ancien format (juste une liste de slotId) : on migre
            return {"notified_slots": data, "last_update_id": None}
        return {
            "notified_slots": data.get("notified_slots", []),
            "last_update_id": data.get("last_update_id"),
        }
    except (FileNotFoundError, json.JSONDecodeError):
        return {"notified_slots": [], "last_update_id": None}


def save_state(notified_ids, last_update_id):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {"notified_slots": sorted(notified_ids), "last_update_id": last_update_id},
            f,
            ensure_ascii=False,
            indent=2,
        )


def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID manquant(s) : message non envoyé.", file=sys.stderr)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True},
        timeout=15,
    )
    if resp.status_code != 200:
        print(f"Échec envoi Telegram: {resp.status_code} {resp.text}", file=sys.stderr)


def get_telegram_messages(last_update_id):
    """Récupère les messages reçus par le bot depuis last_update_id (exclu).
    Retourne (liste_de_messages, nouveau_last_update_id)."""
    if not TELEGRAM_BOT_TOKEN:
        return [], last_update_id
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    params = {"timeout": 0}
    if last_update_id is not None:
        params["offset"] = last_update_id + 1
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        updates = r.json().get("result", [])
    except Exception as e:
        print(f"Erreur getUpdates: {e}", file=sys.stderr)
        return [], last_update_id

    messages = []
    new_last_update_id = last_update_id
    for upd in updates:
        new_last_update_id = upd["update_id"]
        msg = upd.get("message")
        if msg:
            messages.append(msg)
    return messages, new_last_update_id


def format_slot(dt, service):
    price = service.get("price", 0) / 100
    duration = service.get("duration")
    jour = dt.strftime("%A %d/%m").capitalize()
    heure = dt.strftime("%Hh%M")
    return f"{jour} à {heure} ({duration} min) — {price:.2f} €"


def main():
    today = datetime.datetime.now(PARIS).date()
    all_slots = {}  # slotId -> description, TOUTES les disponibilités
    filtered_matches = {}  # slotId -> description, seulement celles qui matchent les critères

    for i in range(LOOKAHEAD_DAYS):
        day = today + datetime.timedelta(days=i)
        date_str = day.isoformat()
        try:
            entries = fetch_day(date_str)
        except Exception as e:
            print(f"Erreur pour {date_str}: {e}", file=sys.stderr)
            continue

        for entry in entries:
            start = entry.get("startDateTime")
            if not start:
                continue
            dt = datetime.datetime.fromisoformat(start)
            for service in entry.get("services", []):
                slot_id = service.get("slotId")
                if not slot_id:
                    continue
                description = format_slot(dt, service)
                all_slots[slot_id] = description
                if matches_criteria(dt):
                    filtered_matches[slot_id] = description

    state = load_state()
    previous_ids = set(state["notified_slots"])
    last_update_id = state["last_update_id"]

    # 1) Vérification manuelle : un nouveau message reçu depuis le dernier passage ?
    messages, new_last_update_id = get_telegram_messages(last_update_id)
    is_first_run = last_update_id is None
    manual_check_requested = bool(messages) and not is_first_run

    if manual_check_requested:
        if all_slots:
            lignes = sorted(all_slots.values())
            texte = (
                "🎾 Disponibilités des 7 prochains jours au Jardin du Luxembourg (sans filtre horaire) :\n\n"
                + "\n".join(f"• {l}" for l in lignes)
                + "\n\nhttps://www.anybuddyapp.com/fr/club/tennis-jardin-du-luxembourg"
            )
        else:
            texte = "Aucun créneau disponible du tout sur les 7 prochains jours, tous horaires confondus."
        send_telegram(texte)
        print(texte)

    # 2) Veille automatique : nouveaux créneaux correspondant aux critères habituels
    current_ids = set(filtered_matches.keys())
    new_ids = current_ids - previous_ids

    if new_ids:
        lignes = [filtered_matches[sid] for sid in new_ids]
        texte = (
            "🎾 Nouveau(x) créneau(x) libre(s) au Jardin du Luxembourg !\n\n"
            + "\n".join(f"• {l}" for l in lignes)
            + "\n\nRéserve vite, ça part très vite sur ce club :\n"
            "https://www.anybuddyapp.com/fr/club/tennis-jardin-du-luxembourg"
        )
        send_telegram(texte)
        print(texte)
    elif not manual_check_requested:
        print("Aucun nouveau créneau correspondant.")

    save_state(current_ids, new_last_update_id)


if __name__ == "__main__":
    main()

Variables d'environnement requises :
- TELEGRAM_BOT_TOKEN : token du bot Telegram (créé via @BotFather)
- TELEGRAM_CHAT_ID   : id du chat/utilisateur à notifier

État persistant : notified_slots.json (liste des slotId déjà signalés), pour
n'envoyer un message que pour les créneaux réellement nouveaux depuis la
dernière exécution.
"""

import datetime
import json
import os
import sys
from zoneinfo import ZoneInfo

import requests

CLUB_SLUG = "tennis-jardin-du-luxembourg"
ACTIVITY = "tennis"
LOOKAHEAD_DAYS = 7
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notified_slots.json")

PARIS = ZoneInfo("Europe/Paris")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def fetch_day(date_str):
    url = "https://www.anybuddyapp.com/api/v1/availabilities"
    params = {
        "clubSlug": CLUB_SLUG,
        "dateFrom": date_str,
        "dateTo": f"{date_str}T23:59",
        "activity": ACTIVITY,
    }
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, params=params, headers=headers, timeout=15)
    r.raise_for_status()
    return r.json().get("data", [])


def matches_criteria(dt):
    weekday = dt.weekday()  # 0=lundi ... 5=samedi, 6=dimanche
    if weekday >= 5:
        return True  # week-end : tout créneau
    if dt.hour == 8 and dt.minute in (0, 30):
        return True  # matin de semaine
    if dt.hour >= 18:
        return True  # soir de semaine
    return False


def load_previous_ids():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_current_ids(ids):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(ids), f, ensure_ascii=False, indent=2)


def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID manquant(s) : message non envoyé.", file=sys.stderr)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True},
        timeout=15,
    )
    if resp.status_code != 200:
        print(f"Échec envoi Telegram: {resp.status_code} {resp.text}", file=sys.stderr)


def main():
    today = datetime.datetime.now(PARIS).date()
    matches = {}  # slotId -> description lisible

    for i in range(LOOKAHEAD_DAYS):
        day = today + datetime.timedelta(days=i)
        date_str = day.isoformat()
        try:
            entries = fetch_day(date_str)
        except Exception as e:
            print(f"Erreur pour {date_str}: {e}", file=sys.stderr)
            continue

        for entry in entries:
            start = entry.get("startDateTime")
            if not start:
                continue
            dt = datetime.datetime.fromisoformat(start)
            if not matches_criteria(dt):
                continue
            for service in entry.get("services", []):
                slot_id = service.get("slotId")
                if not slot_id:
                    continue
                price = service.get("price", 0) / 100
                duration = service.get("duration")
                jour = dt.strftime("%A %d/%m").capitalize()
                heure = dt.strftime("%Hh%M")
                matches[slot_id] = f"{jour} à {heure} ({duration} min) — {price:.2f} €"

    previous_ids = load_previous_ids()
    current_ids = set(matches.keys())
    new_ids = current_ids - previous_ids

    if new_ids:
        lignes = [matches[sid] for sid in new_ids]
        texte = (
            "🎾 Nouveau(x) créneau(x) libre(s) au Jardin du Luxembourg !\n\n"
            + "\n".join(f"• {l}" for l in lignes)
            + "\n\nRéserve vite, ça part très vite sur ce club :\n"
            "https://www.anybuddyapp.com/fr/club/tennis-jardin-du-luxembourg"
        )
        send_telegram(texte)
        print(texte)
    else:
        print("Aucun nouveau créneau correspondant.")

    save_current_ids(current_ids)


if __name__ == "__main__":
    main()
