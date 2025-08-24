# Unreal Script Editor — User Guide

Welcome! This app helps you **scan, fix, and save** UnrealScript (`.uc`) files, with built-in access to UDK documentation (works offline if the site is down).

---

## What you can do

- **Open** a single `.uc` file or an entire folder of `.uc` files.
- **Scan** for common UnrealScript issues.
- **Preview** highlighted problems in the editor.
- **Fix** issues one by one or all at once (with confirmation prompts).
- **Save** changes (and keep a one-time `.bak` backup of the original).
- **Read docs** inside the app: live website first; automatic **offline fallback** if the site is down.

---

## System requirements

- **Windows 10/11** (64-bit)
- No Python needed. Everything is bundled in the `.exe`.

---

## Getting started

1. **Download/Copy** `UnrealScriptEditor.exe` to any folder you like.  
2. (Optional) If you have offline docs, place a folder named **`docs_udk`** next to the EXE:
   ```
   UnrealScriptEditor.exe
   docs_udk\
     UnrealScriptHome.html
     UnrealScriptReference.html
     ... (any other saved pages)
   ```
3. **Double-click** `UnrealScriptEditor.exe` to launch.

---

## Basic workflow

### Open files or a folder
- **Open .uc**: choose a single UnrealScript file.
- **Open Folder**: scan every `.uc` file in the selected folder (and subfolders).
- The left panel lists **files that have issues**. Click a file to open it.

### Understand issues
- Detected problems appear in the **Issues** list.
- The editor **highlights** problematic code to help you see what will change.

### Fix modes
- **Strict** (default): safest, rule-based fixes only (e.g., missing braces for `cpptext`, required semicolons).
- **Extended fixes** (optional): adds conservative fixes (e.g., add missing `)` for certain control statements, close `struct`/`enum` blocks).  
- **Unmatched ‘(’ fixer** (optional): removes a truly extra `(` that never closes (skips comments/strings).

> **Tip:** Keep **Strict** on for guaranteed-safe fixes. Use **Extended** only when you want the extra help.

### Apply fixes
- **Apply Selected**: fix just the selected issue.
- **Apply All**: fix all auto-fixable issues shown for the current file.
- **Fix All & Save All…**: batch-process all listed files and save the results to a folder you choose.

### Save changes
- **Save** overwrites the current file.  
- **Save As…** writes to a new file.  
- On the **first change** to a file, the app writes an **original backup** as `yourfile.uc.bak` alongside it.

> After saving, a file that no longer has issues **disappears** from the folder’s issue list.

---

## Docs tab (live-first with offline fallback)

- The **Docs** tab opens official UDK pages **inside the app**.
- If the live page fails (site offline / blocked), the app **automatically switches to your local copy** in `docs_udk/` (if present).
- Use the dropdown at the top of the Docs tab to switch between common pages.

---

## Troubleshooting

**Build or launch errors on OneDrive**  
- OneDrive can lock files. If you see “Access is denied” while updating the EXE, close the app and try again outside a OneDrive folder, or pause OneDrive syncing temporarily.

**Docs show a “Just a moment…” page**  
- That’s the live site’s protection page. The app will fall back to your **offline docs** if available. Add your saved HTML pages into `docs_udk/` for best results.

**No issues found, but the code still won’t compile**  
- The app focuses on common, **rule-bounded** issues. Some errors (semantic/engine-specific) require manual review in the Unreal toolchain. Keep Strict mode on for safety; try Extended for additional help.

**Where are backups?**  
- When you modify a file and save for the first time, the app creates `filename.uc.bak` next to it. You can delete the `.bak` if you don’t need it.

**Everything is local**  
- The app processes files **on your machine** and doesn’t upload your code anywhere.

---

## Uninstall

- Just delete `UnrealScriptEditor.exe`.  
- Optionally remove the `docs_udk` folder and any `.bak` backups you no longer need.

---

## Tips

- For large batches, use **Open Folder** → **Fix All & Save All…**, then select an **output folder** to keep originals untouched.
- Keep **Strict** mode enabled for production code; toggle **Extended** only when you want additional automated help.
