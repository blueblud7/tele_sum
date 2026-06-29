"""messages 롤링 정리 — N일 지난 원본 메시지 삭제 (기본 60일).

원본은 텔레그램에 영구 보존되므로 사본은 대시보드가 보는 윈도우(최대 14일)보다
넉넉히만 유지하면 된다. Supabase 부피 무한 증가 방지.

사용:
    python prune_messages.py            # 60일 초과 삭제
    python prune_messages.py --days 90
"""
import argparse
import db


def prune(days: int) -> int:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM messages WHERE posted_at < NOW() - make_interval(days => %s)",
            (days,),
        )
        return cur.rowcount or 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=60, help="보관 일수 (기본 60)")
    args = p.parse_args()
    n = prune(args.days)
    print(f"messages 정리: {args.days}일 초과 {n}건 삭제")
    db.close()


if __name__ == "__main__":
    main()
