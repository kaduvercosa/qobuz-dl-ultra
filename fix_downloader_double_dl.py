with open("qobuz_dl/downloader.py", "r") as f:
    content = f.read()

old_double_dl = """                # Note: To write in order, we need to gather or wait for them in order
                tasks_seg = [fetch_and_write(i) for i in range(2, n_segments + 1)]
                for coroutine in asyncio.as_completed(tasks_seg):
                    if abort_event.is_set():
                        break
                    # Wait, as_completed yields out of order!
                    # For audio segments, writing them out of order to the file will corrupt the FLAC file unless we seek.
                    # Since we don't know the exact size of decrypted segments beforehand, we must decrypt and write sequentially.
                    pass

                # Therefore, we fetch concurrently but write sequentially.
                tasks_seg = [asyncio.create_task(fetch_and_write(i)) for i in range(2, n_segments + 1)]
                for task in tasks_seg:
                    seg_data = await task
                    if not abort_event.is_set():
                        await file.write(_decrypt_qobuz_segment(seg_data, raw_key, segment_uuid))"""

new_write_sequential = """                # For audio segments, writing them out of order to the file will corrupt the FLAC file unless we seek.
                # Since we don't know the exact size of decrypted segments beforehand, we must decrypt and write sequentially.
                tasks_seg = [asyncio.create_task(fetch_and_write(i)) for i in range(2, n_segments + 1)]
                for task in tasks_seg:
                    seg_data = await task
                    if not abort_event.is_set():
                        await file.write(_decrypt_qobuz_segment(seg_data, raw_key, segment_uuid))"""

content = content.replace(old_double_dl, new_write_sequential)

with open("qobuz_dl/downloader.py", "w") as f:
    f.write(content)
