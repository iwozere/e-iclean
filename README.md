<div align="center">

# E-iClean

**Get your iPhone photos and videos onto your Windows PC — reliably, without a
subscription, without losing progress, and without deleting anything until it's
safely copied.**

</div>

---

## Why E-iClean?

If you've ever tried to copy a few thousand photos off an iPhone onto a Windows PC,
you've probably hit one of these:

- **iCloud** works, but it costs a monthly subscription and keeps your photos in the
  cloud instead of actually freeing up space on your phone.
- **iTunes / the Apple Devices app + Windows Photos import** often freezes or
  disconnects partway through a large library — and when it does, you're stuck
  starting the whole import over from scratch.
- **Third-party "iPhone manager" tools** (iMazing, AnyTrans, CopyTrans, etc.) are paid,
  cluttered with unrelated features, and don't treat "safely delete the originals" as
  a first-class, careful step.

E-iClean does one thing well: **copy everything in your Camera Roll to a folder you
choose on your PC, verify every file actually made it over intact, and only then let
you free up space on your phone** — in a batch you explicitly confirm.

## What it does

- Copies all photos and videos from your iPhone's Camera Roll to a local folder on
  your Windows PC, over a USB cable.
- **Survives a dropped cable or a closed laptop lid.** If the connection is
  interrupted mid-transfer, E-iClean picks up where it left off the moment you
  reconnect — it doesn't restart the whole transfer, and it doesn't lose progress if
  you quit the app or reboot your PC.
- **Never deletes anything from your phone until it's verified.** Every file is
  checked (size and checksum) against what's on your phone before it's ever offered
  up for deletion.
- **Deleting is always your call.** Freeing up space on your phone is a separate,
  explicit step with a clear "here's what will be deleted and how much space you'll
  get back" confirmation — never automatic.
- **Handles Live Photos correctly.** The photo and its motion clip are always kept and
  deleted together, never split.
- **Skips what's already there.** Run it again next month and it only transfers the
  new photos since last time.
- **No account, no subscription, no cloud.** Everything happens locally between your
  phone and your PC over the USB cable you already own.
- **No app to install on your iPhone** — just the standard "Trust This Computer"
  prompt you'd see with any first-time USB connection to a computer.

## Clean My Library — find duplicate photos anywhere on your PC

E-iClean isn't only for photos coming off your iPhone. Its **Clean My Library** mode
lets you point it at *any* folder on your computer — years of accumulated photos from
any source — and finds:

- **Exact duplicates** — the same photo saved more than once.
- **Near-duplicates** — the same shot saved twice with small differences (re-exported
  by a different app, slightly recompressed, and similar), which a simple file
  comparison would miss.

Nothing is ever deleted automatically. You review every match — with a full-size
side-by-side comparison view for a real look before deciding, not just a tiny
thumbnail — and pick exactly what to remove. And by default, "removing" a photo here
doesn't delete it outright: it's moved into a sibling `-delete` folder next to the one
you scanned, so you can double-check (or undo, by just moving it back) before clearing
it out for good. Permanent deletion is available too, as an explicit opt-out.

This is completely independent of the iPhone transfer feature above — no iPhone needs
to be connected to use it.

## What it doesn't do (yet)

E-iClean is deliberately focused on a small set of jobs for now. Wi-Fi transfer,
automatic blur/screenshot detection, backing up things other than photos and videos,
and macOS support are all on the roadmap but not in this release — see
`docs/project_specification.md` if you're curious about what's planned.

## Requirements

- Windows 10 (21H2 or later) or Windows 11
- An iPhone and a USB (Lightning/USB-C) cable
- Enough free disk space on your PC for the photos/videos you're transferring

No iTunes, no iCloud account, and no software installed on the iPhone itself.

## Installation

E-iClean is under active development and doesn't have a packaged installer yet —
that's the very next milestone (see `docs/DEVELOPMENT.md`). Once it ships, installing
will be: download the Windows installer, run it, connect your iPhone, and go.

If you'd like to try the current in-progress build or contribute, see
`docs/DEVELOPMENT.md` for how to run it from source.

## How it works, in short

1. Plug in your iPhone and unlock it.
2. Tap "Trust" on your phone the first time you connect it to this PC.
3. E-iClean scans your Camera Roll and shows you how many items and how much space
   that is.
4. Pick (or confirm) a destination folder on your PC and click **Start Transfer**.
5. Watch progress as files copy over. If the cable comes loose, reconnect it — the
   transfer resumes automatically, no lost progress.
6. Once everything's copied, E-iClean verifies every file.
7. When you're ready, use **Free Up Space** to review exactly what will be deleted
   from your phone and confirm.

## Privacy

E-iClean is fully local: your photos never leave your phone and your PC. There's
no account, no telemetry, and no network calls beyond talking to your own iPhone over
the USB cable.

## Contributing / technical docs

- `docs/project_specification.md` — the full product spec
- `docs/DEVELOPMENT.md` — how to build and run this project from source
- `docs/ipc_protocol.md` — the internal Rust↔Python communication contract
- `AGENTS.md` — coding conventions for contributors (human or AI)
