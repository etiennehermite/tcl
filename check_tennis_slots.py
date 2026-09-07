#!/usr/bin/env python3
"""
Surveillance des créneaux de tennis disponibles au club "Jardin du Luxembourg"
(Anybuddy) et notification Telegram dès qu'un NOUVEAU créneau correspondant
apparaît.

Critères surveillés :
- Semaine (lundi-vendredi) : créneaux à 8h00 ou 8h30 (matin), ou à partir de
  18h00 inclus (soir)
- Week-end (samedi-dimanche) : tout créneau, à n'importe quelle heure

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
