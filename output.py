import json
from datetime import datetime
from pathlib import Path
from colorama import Fore, init
init(autoreset=True)


def save_json_output(signals: dict, output_dir: str = ".") -> str:
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    filename = Path(output_dir) / f"trading_signals_{date_str}.json"
    payload = {"generated_at": datetime.utcnow().isoformat() + "Z", "signals": signals}
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"{Fore.GREEN}  Saved: {filename}")
    return str(filename)
