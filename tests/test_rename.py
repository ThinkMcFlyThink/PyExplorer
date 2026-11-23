"""
Tests for bulk renaming: every rule, conflict detection, and the safe
renaming itself (swaps, chains, case-only changes, undo).

    python tests/test_rename.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ["PYEXPLORER_CONFIG_DIR"] = tempfile.mkdtemp()

from explorer.core.connections import Connections  # noqa: E402
from explorer.core.events import EventBus  # noqa: E402
from explorer.core.rename_rules import RenameItem, RenameRules, name_problem, new_name, plan  # noqa: E402
from explorer.core.storage import JsonStore  # noqa: E402
from explorer.services.bulk_rename import rename_all, undo_pairs  # noqa: E402
from explorer.vfs.registry import VfsRegistry  # noqa: E402
from explorer.vfs.sftp import CredentialsProvider  # noqa: E402


def check(cond, message):
    if not cond:
        raise AssertionError(message)
    print("  ok -", message)


def nn(name, is_dir=False, index=0, **rules):
    return new_name(name, is_dir, index, RenameRules(**rules))


print("rules, one at a time")
check(nn("photo.jpg") == "photo.jpg", "no rules: unchanged")
check(nn("IMG_0042.JPG", name_mode="fixed", name_text="Rome") == "Rome.JPG", "Name: fixed text (extension kept)")
check(nn("abc.txt", name_mode="reverse") == "cba.txt", "Name: reverse")
check(nn("abc.txt", name_mode="remove", num_mode="prefix") == "1.txt", "Name: remove (numbering supplies a name)")
check(nn("IMG_0042.jpg", replace_find="img_", replace_with="Holiday ") == "Holiday 0042.jpg",
      "Replace: case-insensitive by default")
check(nn("IMG_0042.jpg", replace_find="img_", replace_with="x", replace_case=True) == "IMG_0042.jpg",
      "Replace: match case")
check(nn("a.b.c.txt", replace_find=".", replace_with=" ") == "a b c.txt", "Replace: dots in the name, not the extension")
check(nn("IMG_0042.jpg", replace_find=r"IMG_(\d+)", replace_with=r"Photo \1", replace_regex=True) == "Photo 0042.jpg",
      "Replace: regular expression with groups")
check(nn("tWo WoRdS.TXT", case_mode="lower") == "two words.TXT", "Case: lower (extension untouched)")
check(nn("two words.txt", case_mode="upper") == "TWO WORDS.txt", "Case: UPPER")
check(nn("the best-of_rome (final).txt", case_mode="title") == "The Best-Of_Rome (Final).txt", "Case: Title")
check(nn("HELLO world.txt", case_mode="sentence") == "Hello world.txt", "Case: Sentence")
check(nn("Hello.txt", case_mode="invert") == "hELLO.txt", "Case: iNVERT")
check(nn("123456.txt", remove_first=2) == "3456.txt", "Remove: first N")
check(nn("123456.txt", remove_last=2) == "1234.txt", "Remove: last N")
check(nn("123456.txt", remove_from=2, remove_to=4) == "156.txt", "Remove: From 2 To 4")
check(nn("123456.txt", remove_from=3) == "12456.txt", "Remove: From only = one character")
check(nn("song - artist - live.mp3", crop_mode="before", crop_text="artist") == "artist - live.mp3",
      "Remove: crop before a text")
check(nn("song - artist - live.mp3", crop_mode="after", crop_text="artist") == "song - artist.mp3",
      "Remove: crop after a text")
check(nn("a_b-c d.txt", remove_chars="_-") == "abc d.txt", "Remove: given characters")
check(nn("track01_v2.mp3", remove_digits=True) == "track_v.mp3", "Remove: digits")
check(nn("  __name  .txt", remove_trim=True) == "name.txt", "Remove: trim spaces and _ . - at both ends")
check(nn("a   b.txt", remove_double_spaces=True) == "a b.txt", "Remove: double spaces")
check(nn("2024Rome.jpg", move_mode="move", move_what="first", move_count=4, move_to="end", move_sep=" ")
      == "Rome 2024.jpg", "Move: first N characters to the end, with a separator")
check(nn("Rome 2024.jpg", move_mode="move", move_what="last", move_count=4, move_to="start", move_sep=" - ")
      == "2024 - Rome .jpg", "Move: last N to the start with a separator")
check(nn("Rome.jpg", move_mode="copy", move_what="first", move_count=2, move_to="end") == "RomeRo.jpg",
      "Copy: keeps the original characters")
check(nn("abcdef.txt", move_mode="move", move_what="first", move_count=2, move_to="position", move_pos=3)
      == "cdabef.txt", "Move: to a position")
check(nn("photo.jpg", add_prefix="2024 ", add_suffix=" (1)") == "2024 photo (1).jpg", "Add: prefix and suffix")
check(nn("photo.jpg", add_insert="-X-", add_insert_pos=3) == "ph-X-oto.jpg", "Add: insert at position 3")
check(nn("photo.jpg", add_insert="-", add_insert_pos=-1) == "phot-o.jpg", "Add: negative position counts from the end")
check(nn("HolidayPhotoRome2024.jpg", add_word_space=True) == "Holiday Photo Rome 2024.jpg", "Add: word spaces")
check(nn("a.jpg", 0, 0, num_mode="suffix", num_pad=3, num_sep="_") == "a_001.jpg" and
      nn("a.jpg", False, 4, num_mode="suffix", num_pad=3, num_sep="_") == "a_005.jpg", "Numbering: suffix, padded")
check(nn("a.jpg", False, 2, num_mode="prefix", num_start=10, num_step=5, num_sep=". ") == "20. a.jpg",
      "Numbering: start and step")
check(nn("abc.jpg", False, 0, num_mode="insert", num_pos=2) == "a1bc.jpg", "Numbering: insert at position")
check(nn("a.JPEG", ext_mode="lower") == "a.jpeg" and nn("a.jpg", ext_mode="upper") == "a.JPG", "Extension: case")
check(nn("a.txt", ext_mode="fixed", ext_text=".md") == "a.md" and nn("a.txt", ext_mode="remove") == "a",
      "Extension: new / remove")
check(nn("a.tar", ext_mode="extra", ext_text="gz") == "a.tar.gz", "Extension: extra")
check(nn("Folder.v2", True, case_mode="upper") == "FOLDER.V2", "folders have no extension")
check(nn(".bashrc", case_mode="upper") == ".BASHRC", "dot-files have no extension")
check(nn("IMG_0042.JPG", 0, replace_find="IMG_", replace_with="", case_mode="title", add_prefix="Rome ",
         num_mode="suffix", num_pad=2, ext_mode="lower") == "Rome 0042 01.jpg",
      "rules combine in order: Replace -> Case -> Add -> Numbering -> Extension")

print("checking a batch")
items = [RenameItem(f"/x/{n}", n, "/x") for n in ("a.txt", "b.txt", "c.txt")]
rows = plan(items, RenameRules(name_mode="fixed", name_text="same"))
check([r.status for r in rows] == ["error"] * 3 and "Same new name" in rows[0].message,
      "two files getting the same name is caught")
rows = plan(items, RenameRules(num_mode="suffix"), exists=lambda p, n: n == "b 2.txt")
check([r.status for r in rows] == ["rename", "error", "rename"] and "already exists" in rows[1].message,
      "a name already taken on disk is caught")
chain = [RenameItem("/x/1.txt", "1.txt", "/x"), RenameItem("/x/2.txt", "2.txt", "/x")]
rows = plan(chain, RenameRules(name_mode="remove", num_mode="prefix", num_start=2),
            exists=lambda p, n: n in ("1.txt", "2.txt"))
check([r.new for r in rows] == ["2.txt", "3.txt"] and all(r.status == "rename" for r in rows),
      "a name freed by another item of the batch is fine (1 -> 2, 2 -> 3)")
keep = [RenameItem("/x/a.txt", "a.txt", "/x", checked=False), RenameItem("/x/b.txt", "b.txt", "/x")]
rows = plan(keep, RenameRules(replace_find="b", replace_with="a"))
check(rows[0].status == "skip" and rows[1].status == "error" and "a.txt already has" in rows[1].message,
      "unticked items keep their name - and block it")
rows = plan([RenameItem(f"/x/{n}", n, "/x") for n in ("a.txt", "b.txt")], RenameRules(num_mode="prefix"))
check([r.new for r in rows] == ["1 a.txt", "2 b.txt"], "numbering follows the list order")
rows = plan([RenameItem("/x/a.txt", "a.txt", "/x", checked=False), RenameItem("/x/b.txt", "b.txt", "/x")],
            RenameRules(num_mode="prefix"))
check(rows[1].new == "1 b.txt", "...and counts only ticked items")
check(name_problem("a/b") and name_problem("") and name_problem("..") and name_problem("x" * 300),
      "invalid names are caught (/, empty, .., too long)")
check(name_problem("a:b", windows=True) and name_problem("CON.txt", windows=True) and name_problem("a.", windows=True),
      "Windows rules: <>:\"|?*, reserved names, trailing dots")
check(plan([RenameItem("/x/A.txt", "A.txt", "/x")], RenameRules(case_mode="lower"),
           exists=lambda p, n: True, case_insensitive=True)[0].status == "rename",
      "a case-only change is allowed where names ignore case")
check(plan([RenameItem("/x/a.txt", "a.txt", "/x")], RenameRules(add_suffix="x"))[0].status == "rename",
      "a plain rename is OK")

print("renaming on disk")
bus = EventBus()
vfs = VfsRegistry(Connections(JsonStore(os.environ["PYEXPLORER_CONFIG_DIR"] + "/c.json"), bus), CredentialsProvider())
root = tempfile.mkdtemp(prefix="pyx-rename-")


def make(*names):
    for n in names:
        with open(os.path.join(root, n), "w") as fh:
            fh.write(n)


def content(n):
    with open(os.path.join(root, n)) as fh:
        return fh.read()


make("a.txt", "b.txt", "c.txt")
P = lambda n: os.path.join(root, n)   # noqa: E731
res = rename_all(vfs, [(P("a.txt"), P("b.txt")), (P("b.txt"), P("a.txt"))])
check(not res.errors and content("a.txt") == "b.txt" and content("b.txt") == "a.txt", "a swap (a <-> b) works")
res = rename_all(vfs, [(P("a.txt"), P("b.txt")), (P("b.txt"), P("c.txt")), (P("c.txt"), P("d.txt"))])
check(not res.errors and sorted(os.listdir(root)) == ["b.txt", "c.txt", "d.txt"] and content("d.txt") == "c.txt"
      and content("b.txt") == "b.txt", "a chain (a -> b -> c -> d) works")
done = res.done
res = rename_all(vfs, undo_pairs(done))
check(not res.errors and sorted(os.listdir(root)) == ["a.txt", "b.txt", "c.txt"] and content("a.txt") == "b.txt",
      "undo puts everything back")
res = rename_all(vfs, [(P("a.txt"), P("A.txt"))], case_insensitive=True)
check(not res.errors and "A.txt" in os.listdir(root), "a case-only change goes through a temporary name")
make("taken.txt")
res = rename_all(vfs, [(P("b.txt"), P("taken.txt")), (P("c.txt"), P("free.txt"))])
check(len(res.errors) == 1 and "already exists" in res.errors[0][1] and content("taken.txt") == "taken.txt"
      and os.path.exists(P("free.txt")), "a clash on disk fails that one only - nothing is overwritten")
check(not any(n.startswith(".pyx-rename") for n in os.listdir(root)), "no temporary names are left behind")
shutil.rmtree(root)
print("\nRENAME TESTS PASSED")
