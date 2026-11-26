"""Incremental NDJSON spooling and bounded-memory, verified final JSON export."""
import json
import os
from pathlib import Path
import shutil
import tempfile


class LiveRecorder:
    def __init__(self, folder):
        folder.mkdir(parents=True, exist_ok=True)
        self.folder = folder
        self.work = Path(tempfile.mkdtemp(prefix='.live-record-', dir=folder))
        self.files = {name: (self.work/(name+'.ndjson')).open('w', encoding='utf-8')
                      for name in ('features', 'events')}
        self.counts = dict(features=0, events=0)
        self.closed = False

    def append(self, name, value):
        self.files[name].write(json.dumps(value, allow_nan=False)+'\n')
        self.counts[name] += 1

    def close(self):
        if not self.closed:
            failure = None
            for file in self.files.values():
                try:
                    file.close()
                except OSError as error:
                    failure = failure or error
            self.closed = True
            if failure:
                raise failure

    def finish(self, metadata):
        import ijson
        self.close()
        metadata = dict(metadata)
        metadata.pop('estimatedBpm', None)
        cutoff = metadata.get('playedDurationSeconds', metadata['durationSeconds'])
        written = dict(features=0, events=0)
        last_bpm = None
        staging = self.work/'complete.json'
        with staging.open('w', encoding='utf-8') as output:
            output.write(json.dumps(metadata, allow_nan=False)[:-1])
            for name in ('features', 'events'):
                output.write(',\n'+json.dumps(name)+':[')
                with (self.work/(name+'.ndjson')).open(encoding='utf-8') as source:
                    for line in source:
                        value = json.loads(line)  # Validate each record without retaining the array.
                        if value.get('detectedAtSeconds', value['timeSeconds']) > cutoff+1e-9:
                            continue
                        if written[name]:
                            output.write(',\n')
                        json.dump(value, output, allow_nan=False)
                        written[name] += 1
                        if name == 'features':
                            last_bpm = value.get('estimatedBpm')
                output.write(']')
            output.write(',"estimatedBpm":'+json.dumps(last_bpm, allow_nan=False))
            output.write(',"recordCounts":'+json.dumps(written))
            output.write('}\n')
            output.flush()
            os.fsync(output.fileno())
        counts = dict(features=0, events=0)
        with staging.open('rb') as source:
            for prefix, event, value in ijson.parse(source):
                if event == 'start_map' and prefix in ('features.item', 'events.item'):
                    counts[prefix.split('.')[0]] += 1
        if counts != written:
            raise ValueError('Recorded JSON counts did not validate.')
        stem = Path(metadata['source']).stem+'_live_features'
        index = 0
        while True:
            target = self.folder/(stem+(f' ({index})' if index else '')+'.json')
            try:
                with target.open('xb'):
                    pass
                break
            except FileExistsError:
                index += 1
        try:
            os.replace(staging, target)
            with target.open('rb') as saved:
                if saved.read(1) != b'{':
                    raise ValueError('Cannot reopen live JSON.')
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        try:
            shutil.rmtree(self.work)
        except OSError:
            pass  # The verified output is valid even if diagnostic cleanup is denied.
        return target
