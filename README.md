# TeraDrive

Log in to TeraBox, Google Drive and MEGA. TeraDrive merges them into ONE virtual drive (for example T:)
with a big cache on your real SSD.

## Start
1. Double-click `run.bat` (needs Python 3), or run `build_exe.bat` once to make `dist\TeraDrive.exe`.
2. In the Setup box click **Get rclone** and **Install WinFsp** (WinFsp asks for admin).
3. Click **Log in** next to each account.
4. Pick a drive letter, a cache folder on your fastest SSD and a cache size, then **Mount drive**.
5. Install or copy your game to the new drive. Use **Pre-load a folder** to fill the cache before playing.

## How it works
- rclone talks to each cloud. TeraDrive keeps its own rclone settings in `%APPDATA%\TeraDrive`, so your other rclone setups are not touched.
- rclone's `union` remote merges the accounts. Each file lives in ONE account (files are not split), so
  a single file must fit in one account's free space.
- WinFsp shows the merged storage as a drive letter. `--vfs-cache-mode full` keeps files you used on your SSD.

## Know before you play
- It is NOT a real SSD. The first read of any file comes over the internet. Cache hits are fast.
- Google Drive and MEGA log in works with normal rclone. MEGA accounts with 2FA are not supported here.
- TeraBox has no official API, and official rclone has no TeraBox backend. TeraBox only works if you
  have a TeraBox-enabled rclone build (use **Use my rclone.exe**) or a bridge that shows TeraBox as a
  remote named `terabox`. If it breaks, the other two accounts still work.
- "Most free space first" needs each account to report its size. If TeraBox does not, pick
  "Fill the first account first" instead.
- Free cloud plans can throttle speed and may have rules about this kind of use. Anti-cheat may
  block games on a network-style drive.
- Keep a second copy of anything important. Cloud accounts can be locked or deleted.
- Not tested on a real Windows PC yet. If something fails, copy the text from the Activity box.
