#!/usr/bin/env python3
"""从 MySQL 还原文件到服务器磁盘

用法:
    python3 restore.py <远程根目录> [文件路径...]

不指定文件则还原 file_transfer.files 表里的全部文件。
"""
import sys
import os
import subprocess
import hashlib
from pathlib import Path

MYSQL_CMD = ["mysql", "-uroot", "-N", "-B", "-e"]
DB = "file_transfer"


def mysql_query(sql: str) -> str:
    r = subprocess.run(MYSQL_CMD + [sql], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"MySQL 错误: {r.stderr}", file=sys.stderr)
        sys.exit(1)
    return r.stdout


def restore_file(remote_root: str, path: str):
    """还原单个文件"""
    # 用 hex 取出内容，避免二进制问题
    out = mysql_query(f"SELECT HEX(content), size FROM {DB}.files WHERE path = '{path}'")
    lines = [l for l in out.split("\n") if l.strip()]
    if not lines:
        print(f"❌ 表中无此文件: {path}")
        return False

    hex_content, size = lines[0].split("\t")
    content = bytes.fromhex(hex_content)

    target = Path(remote_root) / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)

    ok = len(content) == int(size)
    mark = "✅" if ok else "⚠️ "
    print(f"{mark} {path} ({len(content)} 字节)")
    return ok


def restore_all(remote_root: str):
    """还原全部"""
    out = mysql_query(f"SELECT path FROM {DB}.files ORDER BY path")
    paths = [l.strip() for l in out.split("\n") if l.strip()]
    if not paths:
        print("表中没有文件")
        return 0, 0

    print(f"待还原 {len(paths)} 个文件")
    print("-" * 50)

    ok = fail = 0
    for p in paths:
        try:
            if restore_file(remote_root, p):
                ok += 1
            else:
                fail += 1
        except Exception as e:
            print(f"❌ {p}: {e}")
            fail += 1

    print("-" * 50)
    print(f"完成: 成功 {ok}, 失败 {fail}")
    return ok, fail


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    remote_root = sys.argv[1]
    files = sys.argv[2:]

    if files:
        ok = fail = 0
        for f in files:
            if restore_file(remote_root, f):
                ok += 1
            else:
                fail += 1
        print(f"\n完成: 成功 {ok}, 失败 {fail}")
    else:
        restore_all(remote_root)


if __name__ == "__main__":
    main()
