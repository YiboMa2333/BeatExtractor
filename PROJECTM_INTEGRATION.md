> Historical investigation: the current application exports lightweight ECG videos and feature JSON. Native projectM and the earlier procedural background are outside its active code path. See README.md for current usage.

# projectM investigation and current rendering status

Investigated 26 November 2026. **Native projectM rendering is not integrated or
enabled in this build.** The complete one-run workflow uses the explicitly
permitted procedural fallback. No MilkDrop compatibility is claimed for that
fallback, and copying a DLL into the project does not enable a hypothetical
native backend.

## Environment evidence

- `ctypes.util.find_library('projectM-4')` and `find_library('projectM')`: no library.
- No projectM DLL or `.milk` preset in the project/library locations checked.
- Neither `projectm` nor `pyprojectm` Python module is installed. No existing
  Python OpenGL wrapper is installed.
- A real hidden WGL context was successfully created: **OpenGL 4.6.0, Intel
  Iris Xe Graphics**, driver build 32.0.101.7085. This confirms GL context support,
  not successful projectM preset rendering. The subprocess probe is isolated and
  times out safely; it creates no visible application window.

`background.inspect_environment()` repeats the local capability checks and
records the actual fallback reason. The OpenGL probe is `src/opengl_probe.py`.
Runtime checks are offline. These are local discovery results, not assertions
that every machine or Python package index lacks projectM.

## Documented native interface

The [official projectM repository](https://github.com/projectM-visualizer/projectm)
provides libprojectM for embedding, not a Python MP4-export API. A host application
must supply a current GL context, load effects/textures, feed PCM, render frames,
read pixels and encode them itself.

The [C audio header](https://github.com/projectM-visualizer/projectm/blob/master/src/api/include/projectM-4/audio.h)
defines `projectm_pcm_add_float` for mono or interleaved stereo float samples and
`projectm_pcm_get_max_samples` for its input limit. A future host must split PCM
intervals into supported chunk sizes rather than let samples be discarded.
The [OpenGL header](https://github.com/projectM-visualizer/projectm/blob/master/src/api/include/projectM-4/render_opengl.h)
defines rendering to a framebuffer, not video file export.

Crucially, the [master parameter header](https://github.com/projectM-visualizer/projectm/blob/master/src/api/include/projectM-4/parameters.h)
documents `projectm_set_frame_time` as **since 4.2.0**, allowing an explicit
fractional frame time instead of the system clock. The
[latest stable release checked, 4.1.8](https://github.com/projectM-visualizer/projectm/releases/tag/v4.1.8),
does not declare that function in its
[parameter header](https://github.com/projectM-visualizer/projectm/blob/v4.1.8/src/api/include/projectM-4/parameters.h).
Setting FPS alone is not a replacement for setting offline presentation time.
Using stable native rendering at arbitrary speed without verified time control
would undermine this application's WAV-timeline requirement.

The [official preset documentation](https://github.com/projectM-visualizer/projectm#presets)
describes separate MilkDrop-style equation/shader packs and textures. A particular
preset needs validation with the chosen library/GPU; no blanket compatibility with
MilkDrop 3's additional formats or features is assumed. No preset pack is bundled
or required by this fallback.

## Working phases and limits

1. Existing WAV-to-JSON extraction and verification are preserved.
2. Fully automatic audio-reactive procedural background, JSON-driven overlay and
   H.264/AAC export work at fixed 1080p/30 FPS. PCM is streamed by exact frame
   intervals; frames are streamed to FFmpeg with no playback or sleep-based timing.
3. Native projectM remains disabled: no installed native stack/presets and no
   tested stable external-time API. No speculative ctypes renderer is shipped.
   Native integration needs a pinned compatible build, tested context/FBO
   readback, a validated fixed low-flash preset, and end-to-end timing tests
   before it can replace the fallback.
4. Automated tests run the actual Windows BAT with one WAV input, verify the
   resulting JSON and MP4, and inspect a decoded beat-frame spike.

`create_renderer()` is the boundary between the exporter and background renderer.
Today it returns only the tested `ProceduralBackground`, plus diagnostic metadata.
It does not silently select MilkDrop/projectM based on an installed filename.
The old recording adapter remains available for regression coverage of the prior
Python export API, but the CMD workflow never imports recordings, launches other
apps, or asks for offsets.

This is offline rendering. Frame quantisation, beat-estimation error, and native
engine integration are separate concerns; none establishes a 25 ms real-time
latency result.
