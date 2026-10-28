import json
import os
import shutil
import time
import uuid
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.utils.text import get_valid_filename


class ChunkedUploadedFile(UploadedFile):
    """
    A Django UploadedFile wrapper around a file assembled from chunks.
    Compatible with form fields, zipfile, PIL, FFmpeg, etc.
    """
    def __init__(self, file_path, original_filename, content_type='application/octet-stream'):
        file_obj = open(file_path, 'rb')
        size = os.path.getsize(file_path)
        super().__init__(file=file_obj, name=original_filename, content_type=content_type, size=size)
        self.file_path = file_path

    def temporary_file_path(self):
        return self.file_path

    def close(self):
        if not self.file.closed:
            self.file.close()


class ChunkedUploadManager:
    @staticmethod
    def get_base_dir():
        base = os.path.join(settings.MEDIA_ROOT, 'chunked_uploads')
        os.makedirs(base, exist_ok=True)
        return base

    @staticmethod
    def validate_uuid(val):
        try:
            val_uuid = uuid.UUID(str(val), version=4)
            return str(val_uuid)
        except (ValueError, AttributeError, TypeError):
            raise ValueError('Invalid UUID')

    @classmethod
    def get_upload_dir(cls, user_id, upload_id):
        valid_id = cls.validate_uuid(upload_id)
        path = os.path.join(cls.get_base_dir(), 'chunks', str(user_id), valid_id)
        os.makedirs(path, exist_ok=True)
        return path

    @classmethod
    def get_assembled_dir(cls, user_id, upload_token):
        valid_token = cls.validate_uuid(upload_token)
        path = os.path.join(cls.get_base_dir(), 'assembled', str(user_id), valid_token)
        os.makedirs(path, exist_ok=True)
        return path

    @classmethod
    def save_chunk(cls, user_id, upload_id, chunk_index, total_chunks, chunk_file):
        try:
            chunk_index = int(chunk_index)
            total_chunks = int(total_chunks)
        except (ValueError, TypeError):
            raise ValueError('Invalid chunk indices')

        if chunk_index < 0 or chunk_index >= total_chunks:
            raise ValueError(f'chunk_index {chunk_index} out of bounds for total {total_chunks}')

        upload_dir = cls.get_upload_dir(user_id, upload_id)
        chunk_path = os.path.join(upload_dir, f'chunk_{chunk_index}')

        with open(chunk_path, 'wb') as dest:
            for chunk in chunk_file.chunks():
                dest.write(chunk)

        meta_path = os.path.join(upload_dir, 'meta.json')
        if not os.path.exists(meta_path):
            with open(meta_path, 'w') as f:
                json.dump({
                    'user_id': user_id,
                    'upload_id': str(upload_id),
                    'total_chunks': total_chunks,
                    'created_at': time.time(),
                }, f)

        return True

    @classmethod
    def assemble_chunks(cls, user_id, upload_id, original_filename, total_chunks):
        try:
            total_chunks = int(total_chunks)
        except (ValueError, TypeError):
            raise ValueError('Invalid total_chunks')

        upload_dir = cls.get_upload_dir(user_id, upload_id)

        # Verify all chunk files exist
        for i in range(total_chunks):
            chunk_file = os.path.join(upload_dir, f'chunk_{i}')
            if not os.path.exists(chunk_file):
                raise ValueError(f'Missing chunk {i} of {total_chunks}')

        upload_token = str(uuid.uuid4())
        assembled_dir = cls.get_assembled_dir(user_id, upload_token)
        safe_filename = get_valid_filename(original_filename) or 'upload.bin'
        assembled_file_path = os.path.join(assembled_dir, safe_filename)

        total_size = 0
        with open(assembled_file_path, 'wb') as outfile:
            for i in range(total_chunks):
                chunk_file = os.path.join(upload_dir, f'chunk_{i}')
                with open(chunk_file, 'rb') as infile:
                    while True:
                        buf = infile.read(64 * 1024)
                        if not buf:
                            break
                        outfile.write(buf)
                        total_size += len(buf)

        meta = {
            'upload_token': upload_token,
            'filename': original_filename,
            'safe_filename': safe_filename,
            'size': total_size,
            'user_id': user_id,
            'created_at': time.time(),
        }
        with open(os.path.join(assembled_dir, 'meta.json'), 'w') as f:
            json.dump(meta, f)

        # Cleanup raw chunks
        shutil.rmtree(upload_dir, ignore_errors=True)

        return meta

    @classmethod
    def get_assembled_file(cls, user_id, upload_token):
        try:
            cls.validate_uuid(upload_token)
        except ValueError:
            return None

        assembled_dir = os.path.join(cls.get_base_dir(), 'assembled', str(user_id), str(upload_token))
        meta_path = os.path.join(assembled_dir, 'meta.json')
        if not os.path.exists(meta_path):
            return None

        try:
            with open(meta_path, 'r') as f:
                meta = json.load(f)
        except Exception:
            return None

        safe_filename = meta.get('safe_filename')
        if not safe_filename:
            return None

        file_path = os.path.join(assembled_dir, safe_filename)
        if not os.path.exists(file_path):
            return None

        return ChunkedUploadedFile(file_path, meta.get('filename', safe_filename))

    @classmethod
    def cleanup_old_chunks(cls, max_age_hours=24):
        now = time.time()
        max_age_seconds = max_age_hours * 3600
        base_dir = cls.get_base_dir()

        for category in ['chunks', 'assembled']:
            cat_dir = os.path.join(base_dir, category)
            if not os.path.exists(cat_dir):
                continue
            for user_id in os.listdir(cat_dir):
                user_path = os.path.join(cat_dir, user_id)
                if not os.path.isdir(user_path):
                    continue
                for item in os.listdir(user_path):
                    item_path = os.path.join(user_path, item)
                    if not os.path.isdir(item_path):
                        continue
                    try:
                        mtime = os.path.getmtime(item_path)
                        if now - mtime > max_age_seconds:
                            shutil.rmtree(item_path, ignore_errors=True)
                    except OSError:
                        pass
