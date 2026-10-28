import json
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.views import View

from judge.utils.chunked_upload import ChunkedUploadManager


class ChunkUploadView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        upload_id = request.POST.get('upload_id')
        chunk_index = request.POST.get('chunk_index')
        total_chunks = request.POST.get('total_chunks')
        chunk_file = request.FILES.get('chunk_file')

        if not upload_id or chunk_index is None or total_chunks is None or not chunk_file:
            return JsonResponse({'success': False, 'error': 'Missing required fields'}, status=400)

        try:
            ChunkedUploadManager.save_chunk(
                user_id=request.user.id,
                upload_id=upload_id,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                chunk_file=chunk_file,
            )
            return JsonResponse({'success': True, 'chunk_index': int(chunk_index)})
        except ValueError as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
        except Exception as e:
            return JsonResponse({'success': False, 'error': 'Failed to save chunk'}, status=500)


class ChunkCompleteView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        upload_id = request.POST.get('upload_id')
        filename = request.POST.get('filename') or 'upload.bin'
        total_chunks = request.POST.get('total_chunks')
        expected_size = request.POST.get('expected_size')

        if not upload_id or total_chunks is None:
            return JsonResponse({'success': False, 'error': 'Missing required fields'}, status=400)

        try:
            meta = ChunkedUploadManager.assemble_chunks(
                user_id=request.user.id,
                upload_id=upload_id,
                original_filename=filename,
                total_chunks=total_chunks,
                expected_size=expected_size,
            )
            return JsonResponse({
                'success': True,
                'upload_token': meta['upload_token'],
                'filename': meta['filename'],
                'size': meta['size'],
            })
        except ValueError as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
        except Exception as e:
            return JsonResponse({'success': False, 'error': 'Failed to assemble chunks'}, status=500)
