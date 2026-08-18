# Leave tag_function in run_in_executor since it's doing sync blocking I/O (mutagen, etc)
# Leave verify_audio_integrity and inject_lyrics in run_in_executor as well since they are sync blocking functions.
