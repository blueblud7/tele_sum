import json
import os

SELECTION_FILE = "selected_channels.json"


def load_selection() -> list[int] | None:
    if not os.path.exists(SELECTION_FILE):
        return None
    with open(SELECTION_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_selection(channel_ids: list[int]):
    with open(SELECTION_FILE, "w", encoding="utf-8") as f:
        json.dump(channel_ids, f, ensure_ascii=False, indent=2)


def display_channels(dialogs: list) -> None:
    print("\n" + "=" * 60)
    print("구독 중인 채널/그룹 목록")
    print("=" * 60)
    for i, dialog in enumerate(dialogs, 1):
        entity = dialog.entity
        type_label = "채널" if hasattr(entity, "broadcast") and entity.broadcast else "그룹"
        username = getattr(entity, "username", None)
        username_str = f"  @{username}" if username else ""
        print(f"  {i:3}. [{type_label}] {dialog.name}{username_str}")
    print("=" * 60)


def select_channels(dialogs: list) -> list:
    display_channels(dialogs)
    print("\n선택 방법:")
    print("  - 번호 입력 (쉼표 구분): 1,3,5")
    print("  - 범위 입력: 1-10")
    print("  - 전체 선택: all")
    print()

    while True:
        raw = input("채널 선택 > ").strip()
        if not raw:
            continue

        if raw.lower() == "all":
            return dialogs

        selected = []
        try:
            for part in raw.split(","):
                part = part.strip()
                if "-" in part:
                    start, end = part.split("-", 1)
                    for i in range(int(start), int(end) + 1):
                        if 1 <= i <= len(dialogs):
                            selected.append(dialogs[i - 1])
                else:
                    i = int(part)
                    if 1 <= i <= len(dialogs):
                        selected.append(dialogs[i - 1])
        except ValueError:
            print("올바른 형식으로 입력해주세요.")
            continue

        if not selected:
            print("선택된 채널이 없습니다. 다시 입력해주세요.")
            continue

        print(f"\n선택된 채널 {len(selected)}개:")
        for d in selected:
            print(f"  - {d.name}")

        confirm = input("\n이대로 진행할까요? (y/n/r=재선택) > ").strip().lower()
        if confirm == "y":
            return selected
        elif confirm == "r":
            continue
        else:
            continue
