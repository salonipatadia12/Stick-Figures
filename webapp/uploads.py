"""Streamed multipart upload for the worker: the `file` part goes straight to a temp file on disk (never held in
memory), with a hard size limit; small form fields (`name`, `kind`) are kept in memory."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from python_multipart.multipart import MultipartParser, parse_options_header

FIELD_LIMIT = 4096


class UploadError(Exception):
    def __init__(self, message, status=400):
        self.status = status
        super().__init__(message)


async def receive_upload(request, tmp_dir: Path, limit: int):
    """(fields dict, temp file path or None, file size). The caller renames or deletes the temp file."""
    ctype, params = parse_options_header(request.headers.get('content-type', ''))
    if ctype != b'multipart/form-data' or b'boundary' not in params:
        raise UploadError('send multipart/form-data with a "file" part and "name", "kind" fields')
    length = request.headers.get('content-length')
    if length and length.isdigit() and int(length) > limit + 64 * 1024:
        raise UploadError(f'file too large (limit {limit // (1024 * 1024)} MB)', 413)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    state = dict(header_field=b'', header_value=b'', headers={}, part=None, name=None, data=bytearray(), size=0,
                 error=None)
    fields, out = {}, {}
    fd, tmp = tempfile.mkstemp(prefix='.upload-', dir=tmp_dir)
    fh = os.fdopen(fd, 'wb')

    def on_part_begin():
        state.update(headers={}, name=None, data=bytearray(), part='field')

    def on_header_field(data, start, end):
        state['header_field'] += data[start:end]

    def on_header_value(data, start, end):
        state['header_value'] += data[start:end]

    def on_header_end():
        state['headers'][state['header_field'].lower()] = state['header_value']
        state['header_field'] = state['header_value'] = b''

    def on_headers_finished():
        _, opts = parse_options_header(state['headers'].get(b'content-disposition', b''))
        name = opts.get(b'name', b'').decode('utf-8', 'replace')
        state['name'] = name
        state['part'] = 'file' if (b'filename' in opts or name == 'file') else 'field'
        if state['part'] == 'file':
            if 'file' in out:
                state['error'] = UploadError('send one file per request')
            out['file'] = True
            out['filename'] = opts.get(b'filename', b'').decode('utf-8', 'replace')

    def on_part_data(data, start, end):
        if state['error']:
            return
        chunk = data[start:end]
        if state['part'] == 'file':
            state['size'] += len(chunk)
            if state['size'] > limit:
                state['error'] = UploadError(f'file too large (limit {limit // (1024 * 1024)} MB)', 413)
                return
            fh.write(chunk)
        else:
            state['data'] += chunk
            if len(state['data']) > FIELD_LIMIT:
                state['error'] = UploadError(f'form field {state["name"]} is too long')

    def on_part_end():
        if state['part'] == 'field' and state['name']:
            fields[state['name']] = bytes(state['data']).decode('utf-8', 'replace')

    parser = MultipartParser(params[b'boundary'], dict(
        on_part_begin=on_part_begin, on_header_field=on_header_field, on_header_value=on_header_value,
        on_header_end=on_header_end, on_headers_finished=on_headers_finished, on_part_data=on_part_data,
        on_part_end=on_part_end))
    try:
        async for chunk in request.stream():
            parser.write(chunk)
            if state['error']:
                raise state['error']
        parser.finalize()
        if state['error']:
            raise state['error']
        fh.close()
        if 'file' not in out:
            raise UploadError('no "file" part in the upload')
        return fields, Path(tmp), state['size']
    except BaseException:
        fh.close()
        Path(tmp).unlink(missing_ok=True)
        raise
