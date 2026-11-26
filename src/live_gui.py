"""CPU-only Tk Canvas display; graph objects reused, GUI sampled at <=30 FPS by default."""
from collections import deque
from queue import Empty
import threading


class LiveWindow:
    def __init__(self, engine):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.engine = tk, engine
        self.loopback = bool(getattr(engine, 'is_loopback', False))
        self.root = tk.Tk()
        self.root.title('Super Happy Space 2.0 - live audio beat visualisation')
        self.root.geometry('1040x620')
        self.root.configure(bg='#07101b')
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.status = tk.StringVar(value='Ready - Windows speaker loopback, no microphone' if self.loopback else 'Ready - local file playback only, no microphone')
        self.metadata = tk.StringVar(value=engine.path.name)
        self.warning = tk.StringVar(value='BPM: Estimating... | Yellow: onsets | Cyan: estimated beats')
        tk.Label(self.root, text='Super Happy Space 2.0', bg='#07101b', fg='white', font=('Segoe UI', 22)).pack(pady=12)
        tk.Label(self.root, textvariable=self.metadata, bg='#07101b', fg='#b5dbe2').pack()
        tk.Label(self.root, textvariable=self.warning, bg='#07101b', fg='#f4c86d').pack(pady=8)
        self.canvas = tk.Canvas(self.root, bg='#030a12', highlightthickness=0, height=290)
        self.canvas.pack(fill='both', expand=True, padx=20)
        self.baseline = self.canvas.create_line(0, 0, 1, 0, fill='#1d626c')
        self.bar = self.canvas.create_line(0, 0, 0, 1, fill='#7abac6', width=2)
        self.onset_line = self.canvas.create_line(0, 0, 1, 0, fill='#efbd60', width=2, dash=(4, 3))
        self.beat_line = self.canvas.create_line(0, 0, 1, 0, fill='#45eee1', width=3)
        self.spectrum_canvas = tk.Canvas(self.root, bg='#07101b', highlightthickness=0, height=45)
        self.spectrum_canvas.pack(fill='x', padx=24)
        self.spectrum_bars = [self.spectrum_canvas.create_rectangle(0, 0, 1, 1, fill='#377f94', outline='') for _ in range(32)]
        self.spectrum = None
        tk.Label(self.root, text='Audio beat visualisation - not a medical ECG', bg='#07101b', fg='#7fa5ad').pack(pady=5)
        self.progress = ttk.Progressbar(self.root, maximum=engine.info.frames/engine.info.samplerate)
        self.progress.pack(fill='x', padx=24, pady=8)
        if self.loopback:
            self.progress.pack_forget()  # An open-ended capture has no total duration/percentage.
        controls = tk.Frame(self.root, bg='#07101b')
        controls.pack()
        self.play_button = ttk.Button(controls, text='Start capture' if self.loopback else 'Play', command=self.play)
        self.play_button.pack(side='left', padx=5)
        self.pause_button = ttk.Button(controls, text='Pause analysis' if self.loopback else 'Pause', command=engine.pause)
        self.pause_button.pack(side='left', padx=5)
        self.resume_button = ttk.Button(controls, text='Resume analysis' if self.loopback else 'Resume', command=engine.resume)
        self.resume_button.pack(side='left', padx=5)
        self.stop_button = ttk.Button(controls, text='Stop & save' if self.loopback else 'Stop', command=self.stop)
        self.stop_button.pack(side='left', padx=5)
        tk.Label(self.root, textvariable=self.status, bg='#07101b', fg='#b5dbe2', wraplength=990).pack(pady=12)
        self.pending = deque(maxlen=512)
        self.events = deque(maxlen=2048)
        self.last_row = None
        self.onsets = self.beats = 0
        self.finishing = self.done = self.closing = False
        self.finish_error = None
        self.play_id = self.root.after(50, self.play)
        self.tick_id = self.root.after(0, self.tick)

    def play(self):
        if self.engine.started or self.finishing:
            return
        try:
            self.engine.start()
            self.play_button.configure(state='disabled')
            self.status.set('Capturing Windows output. Play music in any app; Stop or close saves JSON.' if self.loopback
                            else 'Playing. Feature timestamps follow source samples and the output-device clock.')
        except Exception as error:
            self.status.set(str(error))
            self.finish_error = str(error)
            self.finalize(False)

    def stop(self):
        self.engine.request_stop()
        self.finalize(False)

    def finalize(self, completed):
        if self.finishing:
            return
        self.finishing = True
        for button in (self.play_button, self.pause_button, self.resume_button, self.stop_button):
            button.configure(state='disabled')
        self.status.set('Stopping workers and writing/validating JSON...')
        def finish_worker():
            try:
                self.engine.finish(completed)
            except Exception as error:
                self.finish_error = str(error)
            self.done = True
        threading.Thread(target=finish_worker, daemon=True).start()

    def close(self):
        self.closing = True
        if self.done or not self.engine.started:
            self.destroy()
        else:
            self.stop()

    def destroy(self):
        for token in (self.play_id, self.tick_id):
            try:
                self.root.after_cancel(token)
            except self.tk.TclError:
                pass
        self.root.destroy()

    def tick(self):
        engine = self.engine
        if hasattr(engine, 'poll_error') and not self.finishing:
            engine.poll_error()
        position = engine.position_samples()/engine.info.samplerate
        for _ in range(256):
            try:
                self.pending.append(engine.gui_queue.get_nowait())
            except Empty:
                break
        while self.pending and self.pending[0][0]['timeSeconds'] <= position:
            row, events, spectrum = self.pending.popleft()
            self.last_row = row
            self.spectrum = spectrum
            for event in events:
                if event['type'] in ('onset', 'estimatedBeat'):
                    self.events.append(event)
                    self.onsets += event['type'] == 'onset'
                    self.beats += event['type'] == 'estimatedBeat'
        while self.events and self.events[0]['timeSeconds'] < position-7:
            self.events.popleft()
        row = self.last_row
        bpm = f"{row['estimatedBpm']:.1f}" if row and row['estimatedBpm'] is not None else 'Estimating...'
        rms = f"{row['rms']:.4f}" if row else '--'
        centroid = f"{row['spectralCentroidHz']:.0f} Hz" if row and row['spectralCentroidHz'] is not None else '--'
        source = engine.capture_label if self.loopback else engine.path.name
        time_label = f'{position:.2f}s captured' if self.loopback else f'{position:.2f}s / {engine.info.duration:.2f}s'
        self.metadata.set(f'{source} | {time_label} | BPM {bpm}\n'
                          f'RMS {rms} | Centroid {centroid} | Onsets {self.onsets} | Estimated beats {self.beats}')
        stats = engine.stats
        if self.loopback and (stats.get('captureOverflows', 0) or stats['analysisDroppedBuffers']):
            self.warning.set(f"Capture overflows {stats['captureOverflows']}; analysis dropped {stats['analysisDroppedBuffers']} buffers. See JSON diagnostics.")
        elif stats['analysisDroppedBuffers'] or stats['decoderUnderruns'] or stats['playbackUnderruns']:
            self.warning.set(f"Analysis dropped {stats['analysisDroppedBuffers']} buffers; "
                             f"decoder gaps {stats['decoderUnderruns']}; output underruns {stats['playbackUnderruns']}. "
                             'Measurements may be incomplete; see JSON diagnostics.')
        elif stats['processingOverruns']:
            self.warning.set(f"DSP exceeded its block-time budget {stats['processingOverruns']} times. Onsets: yellow; estimated beats: cyan.")
        self.progress['value'] = position
        self.draw(position)
        if not self.finishing and (engine.playback_finished.is_set() or engine.error):
            self.finalize(not engine.error and engine.submitted_samples >= engine.info.frames)
        if self.done:
            if self.closing:
                self.destroy()
                return
            text = f'Saved: {engine.result_path}' if engine.result_path else 'No final JSON saved.'
            if self.finish_error or engine.error:
                text += ' Error: '+str(self.finish_error or engine.error)
            self.status.set(text+' Close this window to return to CMD.')
        self.tick_id = self.root.after(max(1, round(1000/engine.settings.gui_fps)), self.tick)

    def draw(self, position):
        width, height = max(10, self.canvas.winfo_width()), max(10, self.canvas.winfo_height())
        left, right, y = 20, width-20, height*.62
        marker, speed = width*.85, (width-40)/7
        self.canvas.coords(self.baseline, left, y, right, y)
        self.canvas.coords(self.bar, marker, height*.1, marker, height*.9)
        for kind, item in (('onset', self.onset_line), ('estimatedBeat', self.beat_line)):
            points = [left, y]
            for event in self.events:
                if event['type'] != kind:
                    continue
                x = marker+(event['timeSeconds']-position)*speed
                if left+10 <= x <= right-10:
                    amplitude = min(1, event['strength']*2)
                    size = (20+amplitude*75)*(1 if kind == 'estimatedBeat' else .5)
                    points += [x-10, y, x-4, y+size*.12, x, y-size, x+4, y+size*.2, x+10, y]
            points += [right, y]
            self.canvas.coords(item, *points)
        if self.spectrum is not None:
            count = len(self.spectrum)
            bar_width = max(1, self.spectrum_canvas.winfo_width())/32
            for i, item in enumerate(self.spectrum_bars):
                values = self.spectrum[i*count//32:max(i*count//32+1, (i+1)*count//32)]
                level = min(40, float(max(values))*500)
                self.spectrum_canvas.coords(item, i*bar_width, 44-level, (i+1)*bar_width-2, 44)

    def run(self):
        self.root.mainloop()
        return self.engine.result_path, self.finish_error or self.engine.error


def run_live(path, folder, settings=None):
    from live_engine import LiveEngine, require_live_dependencies
    require_live_dependencies()
    engine = LiveEngine(path, folder, settings)
    return LiveWindow(engine).run()


def run_loopback(folder, settings=None):
    from loopback_engine import LoopbackEngine
    engine = LoopbackEngine(folder, settings)
    try:
        return LiveWindow(engine).run()
    finally:
        if not engine.finalized:
            engine.finish()
