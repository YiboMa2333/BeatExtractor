"""Explicit microphone input, sharing capture/DSP/recording with the separate loopback mode."""
from loopback_engine import LoopbackEngine, require_loopback_dependencies
import math
from queue import Full
import numpy as np


def select_microphone(client, api, index=None):
    try:
        device = (client.get_default_wasapi_device(d_in=True) if index is None
                  else client.get_device_info_by_index(index))
    except (OSError, LookupError) as error:
        raise RuntimeError('No usable Windows microphone/input device. Check Windows input settings and microphone access.') from error
    if (device.get('isLoopbackDevice') or device.get('maxInputChannels',0) < 1
            or client.get_host_api_info_by_index(device['hostApi'])['type'] != api.paWASAPI):
        raise ValueError('Microphone mode requires a WASAPI input endpoint, not a speaker-loopback device.')
    return device


def list_microphones():
    api = require_loopback_dependencies()
    client = api.PyAudio()
    try:
        host = client.get_host_api_info_by_type(api.paWASAPI)
        default = host['defaultInputDevice']
        return [dict(index=i,name=d['name'],sampleRate=d['defaultSampleRate'],default=i==default)
                for i in range(client.get_device_count())
                for d in [client.get_device_info_by_index(i)]
                if d['hostApi']==host['index'] and d['maxInputChannels']>0 and not d.get('isLoopbackDevice')]
    finally:
        client.terminate()


class MicrophoneEngine(LoopbackEngine):
    is_loopback = False
    is_microphone = True
    source_name = 'microphone'
    source_type = 'windows.wasapi.microphone'

    def __init__(self,*args,**kwargs):
        self.input_gain_db=0.0
        self.capture_gain=1.0
        super().__init__(*args,**kwargs)

    def set_gain_db(self,value):
        value=float(value)
        if not math.isfinite(value) or not 0 <= value <= 36:
            raise ValueError('Microphone gain must be between 0 and 36 dB.')
        self.input_gain_db=value
        self.capture_gain=10**(value/20)
        if self.started and not self.finalized:
            try:
                self.diagnostics.put_nowait(dict(type='microphoneGainChanged',
                    timeSeconds=self.position_samples()/self.info.samplerate,gainDb=value))
            except Full:
                self.stats['diagnosticDrops']+=1

    def transform_capture_block(self,block):
        # Only a small linear gain/clip operation in the callback, never FFT/disk/GUI.
        # The SAME resulting samples go to recording and analysis; no speaker monitoring.
        if self.capture_gain==1:
            return block
        return np.clip(block*self.capture_gain,-1,1).astype(np.float32,copy=False)

    def capture_metadata(self):
        return dict(finalInputGainDb=self.input_gain_db,
            gain='user-selected digital input gain before both recording and feature analysis; clipped to [-1,1]',
            gainChanges='sample-timestamped microphoneGainChanged events; default 0 dB')

    def select_device(self,client,api,index):
        return select_microphone(client,api,index)
