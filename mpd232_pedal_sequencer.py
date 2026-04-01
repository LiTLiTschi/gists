#!/usr/bin/env python3
"""
MPD232 Pedal-Controlled Sequencer Recorder
===========================================
Uses midi-scripter (pip install midiscripter) to:
- Press sustain pedal (CC64) → start recording incoming MIDI from MPD232
- Release pedal → store the recording as a reusable "clip"
- Sequencer NOTE_ON pulse → trigger time-accurate playback of the clip
- GUI shows status, clip info, playback indicator, and configurable trigger note

Hardware setup:
  - MPD232 MIDI out  → 'MPD232' input port
  - eDrum pedal (CC64 sustain) → same 'MPD232' input port  (or separate port,
    just subscribe handle_pedal to it instead)
  - Sequencer MIDI out → 'Sequencer' input port
  - 'Output' virtual port → your DAW / synth

Install:
  pip install midiscripter

Run:
  python mpd232_pedal_sequencer.py
"""

from midiscripter import *
import threading
import time

# -------- Ports (rename to match your system via the GUI) --------------------
mpd_in   = MidiIn('MPD232')                  # MPD232 + eDrum pedal input
seq_in   = MidiIn('Sequencer')               # Sequencer pulse input
midi_out = MidiOut('Output', virtual=True)   # Playback output → DAW/synth

# -------- State --------------------------------------------------------------
_recording    = False
_clip: list[tuple[float, MidiMsg]] = []      # (relative_time_sec, msg)
_record_start = 0.0
_playback_lock = threading.Lock()

# -------- GUI widgets --------------------------------------------------------
status_label   = GuiText('◉ IDLE')
record_btn     = GuiButton('⏺  Record (hold pedal)')
clear_btn      = GuiButton('🗑  Clear Clip')
clip_info      = GuiText('No clip recorded')
play_indicator = GuiText('► –')
seq_note_label = GuiText('Sequencer trigger note (0-127):')
seq_note_sel   = GuiEditableText('36')  # C2 = 36
GuiWidgetLayout([seq_note_label, seq_note_sel], title='Sequencer trigger')

# -------- Recording: pedal controls start/stop ------------------------------
@mpd_in.subscribe
def handle_mpd(msg: MidiMsg):
    global _recording, _clip, _record_start

    # Sustain pedal = CC 64; value >= 64 means pressed, < 64 means released
    if msg.type == MidiType.CONTROL_CHANGE and msg.data1 == 64:
        if msg.data2 >= 64 and not _recording:
            # ---- pedal pressed: start recording ----
            _clip = []
            _record_start = time.monotonic()
            _recording = True
            status_label.content = '🔴 RECORDING'
            record_btn.content   = '⏹  Stop (release pedal)'
            clip_info.content    = 'Recording…'
            log('Pedal pressed → recording started')
        elif msg.data2 < 64 and _recording:
            # ---- pedal released: store clip ----
            _recording = False
            status_label.content = '◉ IDLE'
            record_btn.content   = '⏺  Record (hold pedal)'
            event_count = len(_clip)
            duration    = round(_clip[-1][0], 3) if _clip else 0
            clip_info.content = (
                f'Clip: {event_count} events, {duration}s'
                if _clip else 'Clip: (empty)'
            )
            log(f'Pedal released → stored {event_count} events ({duration}s)')
        return  # never forward CC64 to output

    # Capture every other message while recording
    if _recording:
        t = time.monotonic() - _record_start
        _clip.append((t, msg.copy()))
        log(f'  rec t={t:.3f}s  {msg}')


# -------- Sequencer trigger: play clip on every NOTE_ON pulse ---------------
@seq_in.subscribe
def handle_seq(msg: MidiMsg):
    try:
        trigger_note = int(seq_note_sel.content)
    except ValueError:
        trigger_note = 36

    if msg.type == MidiType.NOTE_ON and msg.data1 == trigger_note and msg.data2 > 0:
        if not _clip:
            log('Sequencer fired but clip is empty – record something first')
            return
        play_indicator.content = '► PLAYING'
        log('Sequencer pulse → playback triggered')
        threading.Thread(target=_playback_thread, daemon=True).start()


def _playback_thread():
    """Replay the recorded clip with original inter-event timing."""
    with _playback_lock:
        clip_copy = list(_clip)   # atomic snapshot
        last_t    = 0.0
        for rel_t, msg in clip_copy:
            sleep_dur = rel_t - last_t
            if sleep_dur > 0:
                time.sleep(sleep_dur)
            midi_out.send(msg)
            last_t = rel_t
    play_indicator.content = '► –'


# -------- GUI: clear button --------------------------------------------------
@clear_btn.subscribe
def on_clear(_):
    global _clip, _recording
    _clip      = []
    _recording = False
    status_label.content   = '◉ IDLE'
    record_btn.content     = '⏺  Record (hold pedal)'
    clip_info.content      = 'No clip recorded'
    play_indicator.content = '► –'
    log('Clip cleared')


# -------- Launch GUI ---------------------------------------------------------
if __name__ == '__main__':
    start_gui()
