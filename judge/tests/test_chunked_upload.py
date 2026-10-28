import io
import os
import shutil
import uuid
from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

import zipfile
from judge.models import Problem, ProblemData, ProblemGroup, Profile
from judge.utils.chunked_upload import ChunkedUploadManager


class ChunkedUploadTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password')
        self.profile = Profile.objects.create(user=self.user)
        self.other_user = User.objects.create_user(username='otheruser', password='password')
        self.other_profile = Profile.objects.create(user=self.other_user)
        self.staff_user = User.objects.create_superuser(username='admin', email='admin@test.com', password='password')
        self.staff_profile = Profile.objects.create(user=self.staff_user)
        self.group = ProblemGroup.objects.create(name='test-group', full_name='Test Group')
        self.problem = Problem.objects.create(
            code='probdata',
            name='Problem Data Test',
            time_limit=1.0,
            memory_limit=65536,
            points=100,
            group=self.group,
        )

    def tearDown(self):
        chunk_dir = os.path.join(settings.MEDIA_ROOT, 'chunked_uploads')
        if os.path.exists(chunk_dir):
            shutil.rmtree(chunk_dir, ignore_errors=True)

    def test_chunk_upload_requires_authentication(self):
        url = reverse('chunk_upload')
        response = self.client.post(url, {
            'upload_id': str(uuid.uuid4()),
            'chunk_index': 0,
            'total_chunks': 1,
            'chunk_file': SimpleUploadedFile('chunk_0', b'hello'),
        })
        self.assertIn(response.status_code, [302, 403])

    def test_chunk_upload_rejects_invalid_upload_id(self):
        self.client.login(username='testuser', password='password')
        url = reverse('chunk_upload')
        response = self.client.post(url, {
            'upload_id': '../../etc/passwd',
            'chunk_index': 0,
            'total_chunks': 1,
            'chunk_file': SimpleUploadedFile('chunk_0', b'hello'),
        })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json().get('success', False))

    def test_chunk_upload_success(self):
        self.client.login(username='testuser', password='password')
        upload_id = str(uuid.uuid4())
        url = reverse('chunk_upload')

        chunk_data = b'chunk 0 content'
        response = self.client.post(url, {
            'upload_id': upload_id,
            'chunk_index': 0,
            'total_chunks': 2,
            'chunk_file': SimpleUploadedFile('blob', chunk_data),
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get('success'))
        self.assertEqual(data.get('chunk_index'), 0)

    def test_chunk_complete_stitches_file(self):
        self.client.login(username='testuser', password='password')
        upload_id = str(uuid.uuid4())
        upload_url = reverse('chunk_upload')
        complete_url = reverse('chunk_complete')

        part1 = b'Hello, '
        part2 = b'World! '
        part3 = b'Chunked upload works perfectly.'

        for idx, part in enumerate([part1, part2, part3]):
            resp = self.client.post(upload_url, {
                'upload_id': upload_id,
                'chunk_index': idx,
                'total_chunks': 3,
                'chunk_file': SimpleUploadedFile(f'blob_{idx}', part),
            })
            self.assertEqual(resp.status_code, 200)

        complete_resp = self.client.post(complete_url, {
            'upload_id': upload_id,
            'filename': 'greeting.txt',
            'total_chunks': 3,
        })
        self.assertEqual(complete_resp.status_code, 200)
        complete_data = complete_resp.json()
        self.assertTrue(complete_data.get('success'))
        self.assertIn('upload_token', complete_data)
        self.assertEqual(complete_data.get('size'), len(part1 + part2 + part3))
        self.assertEqual(complete_data.get('filename'), 'greeting.txt')

        # Verify manager retrieval
        from judge.utils.chunked_upload import ChunkedUploadManager
        token = complete_data['upload_token']
        assembled_file = ChunkedUploadManager.get_assembled_file(self.user.id, token)
        self.assertIsNotNone(assembled_file)
        self.assertEqual(assembled_file.read(), part1 + part2 + part3)

    def test_chunk_complete_fails_if_chunks_missing(self):
        self.client.login(username='testuser', password='password')
        upload_id = str(uuid.uuid4())
        upload_url = reverse('chunk_upload')
        complete_url = reverse('chunk_complete')

        # Upload only chunk 0 of 2
        self.client.post(upload_url, {
            'upload_id': upload_id,
            'chunk_index': 0,
            'total_chunks': 2,
            'chunk_file': SimpleUploadedFile('blob_0', b'chunk0'),
        })

        complete_resp = self.client.post(complete_url, {
            'upload_id': upload_id,
            'filename': 'incomplete.txt',
            'total_chunks': 2,
        })
        self.assertEqual(complete_resp.status_code, 400)
        self.assertFalse(complete_resp.json().get('success', False))

    def test_user_cannot_access_other_users_chunks(self):
        self.client.login(username='testuser', password='password')
        upload_id = str(uuid.uuid4())
        upload_url = reverse('chunk_upload')
        complete_url = reverse('chunk_complete')

        # testuser uploads chunk
        self.client.post(upload_url, {
            'upload_id': upload_id,
            'chunk_index': 0,
            'total_chunks': 1,
            'chunk_file': SimpleUploadedFile('blob_0', b'secret'),
        })

        # switch to otheruser
        self.client.login(username='otheruser', password='password')
        # otheruser tries to complete testuser's upload
        complete_resp = self.client.post(complete_url, {
            'upload_id': upload_id,
            'filename': 'secret.txt',
            'total_chunks': 1,
        })
        self.assertEqual(complete_resp.status_code, 400)

    def test_problem_data_zip_chunk_upload(self):
        self.client.login(username='admin', password='password')

        # Create valid zip in memory
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, 'w') as zf:
            zf.writestr('1.in', '1 2\n')
            zf.writestr('1.out', '3\n')
        zip_bytes = zip_buf.getvalue()

        # Upload zip using chunk upload endpoints
        upload_id = str(uuid.uuid4())
        self.client.post(reverse('chunk_upload'), {
            'upload_id': upload_id,
            'chunk_index': 0,
            'total_chunks': 1,
            'chunk_file': SimpleUploadedFile('tests.zip', zip_bytes),
        })
        complete_resp = self.client.post(reverse('chunk_complete'), {
            'upload_id': upload_id,
            'filename': 'tests.zip',
            'total_chunks': 1,
        })
        self.assertEqual(complete_resp.status_code, 200)
        token = complete_resp.json()['upload_token']

        # Post to ProblemDataView with the token
        url = reverse('problem_data', args=[self.problem.code])
        response = self.client.post(url, {
            'problem-data-zipfile-token': token,
            'problem-data-grader': 'standard',
            'problem-data-checker': 'standard',
            'problem-data-checker_type': 'default',
            'cases-TOTAL_FORMS': '1',
            'cases-INITIAL_FORMS': '0',
            'cases-MIN_NUM_FORMS': '0',
            'cases-MAX_NUM_FORMS': '1',
            'cases-0-order': '1',
            'cases-0-type': 'C',
            'cases-0-input_file': '1.in',
            'cases-0-output_file': '1.out',
            'cases-0-points': '100',
        })
        self.assertEqual(response.status_code, 302)
        problem_data = ProblemData.objects.get(problem=self.problem)
        self.assertTrue(bool(problem_data.zipfile))

    def _generate_png(self):
        from PIL import Image
        buf = io.BytesIO()
        img = Image.new('RGB', (100, 100), color='blue')
        img.save(buf, format='PNG')
        return buf.getvalue()

    def test_avatar_chunk_upload(self):
        self.client.login(username='testuser', password='password')
        test_png = self._generate_png()

        upload_id = str(uuid.uuid4())
        self.client.post(reverse('chunk_upload'), {
            'upload_id': upload_id,
            'chunk_index': 0,
            'total_chunks': 1,
            'chunk_file': SimpleUploadedFile('avatar.png', test_png),
        })
        complete_resp = self.client.post(reverse('chunk_complete'), {
            'upload_id': upload_id,
            'filename': 'avatar.png',
            'total_chunks': 1,
        })
        self.assertEqual(complete_resp.status_code, 200)
        token = complete_resp.json()['upload_token']

        resp = self.client.post(reverse('edit_avatar'), {
            'avatar_image_token': token,
        })
        self.assertEqual(resp.status_code, 302)
        self.profile.refresh_from_db()
        self.assertTrue(bool(self.profile.avatar))

    def test_background_chunk_upload(self):
        self.client.login(username='testuser', password='password')
        test_png = self._generate_png()

        upload_id = str(uuid.uuid4())
        self.client.post(reverse('chunk_upload'), {
            'upload_id': upload_id,
            'chunk_index': 0,
            'total_chunks': 1,
            'chunk_file': SimpleUploadedFile('bg.png', test_png),
        })
        complete_resp = self.client.post(reverse('chunk_complete'), {
            'upload_id': upload_id,
            'filename': 'bg.png',
            'total_chunks': 1,
        })
        self.assertEqual(complete_resp.status_code, 200)
        token = complete_resp.json()['upload_token']

        resp = self.client.post(reverse('edit_background'), {
            'background_image_token': token,
        })
        self.assertEqual(resp.status_code, 302)
        self.profile.refresh_from_db()
        self.assertTrue(bool(self.profile.background))

    def test_martor_image_uploader_chunk_token(self):
        self.client.login(username='admin', password='password')
        test_png = self._generate_png()

        upload_id = str(uuid.uuid4())
        self.client.post(reverse('chunk_upload'), {
            'upload_id': upload_id,
            'chunk_index': 0,
            'total_chunks': 1,
            'chunk_file': SimpleUploadedFile('content.png', test_png),
        })
        complete_resp = self.client.post(reverse('chunk_complete'), {
            'upload_id': upload_id,
            'filename': 'content.png',
            'total_chunks': 1,
        })
        token = complete_resp.json()['upload_token']

        resp = self.client.post(reverse('martor_image_uploader'), {
            'upload_token': token,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get('status'), 200)
        self.assertTrue('link' in data)

    def test_cleanup_chunks_command(self):
        from django.core.management import call_command
        self.client.login(username='testuser', password='password')
        upload_id = str(uuid.uuid4())
        self.client.post(reverse('chunk_upload'), {
            'upload_id': upload_id,
            'chunk_index': 0,
            'total_chunks': 1,
            'chunk_file': SimpleUploadedFile('test.bin', b'12345'),
        })
        call_command('cleanup_chunks', hours=0)
        chunk_path = os.path.join(settings.MEDIA_ROOT, 'chunked_uploads', 'chunks', str(self.user.id), upload_id)
        self.assertFalse(os.path.exists(chunk_path))

    def test_chunk_complete_idempotent(self):
        self.client.login(username='testuser', password='password')
        upload_id = str(uuid.uuid4())
        self.client.post(reverse('chunk_upload'), {
            'upload_id': upload_id,
            'chunk_index': 0,
            'total_chunks': 1,
            'chunk_file': SimpleUploadedFile('sample.txt', b'Hello world!'),
        })
        resp1 = self.client.post(reverse('chunk_complete'), {
            'upload_id': upload_id,
            'filename': 'sample.txt',
            'total_chunks': 1,
        })
        self.assertEqual(resp1.status_code, 200)
        token1 = resp1.json()['upload_token']

        # Second complete call should return same token and metadata
        resp2 = self.client.post(reverse('chunk_complete'), {
            'upload_id': upload_id,
            'filename': 'sample.txt',
            'total_chunks': 1,
        })
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.json()['upload_token'], token1)

    def test_chunk_complete_size_verification(self):
        self.client.login(username='testuser', password='password')
        upload_id = str(uuid.uuid4())
        self.client.post(reverse('chunk_upload'), {
            'upload_id': upload_id,
            'chunk_index': 0,
            'total_chunks': 1,
            'chunk_file': SimpleUploadedFile('sample.txt', b'12345'),
        })
        # Wrong expected size
        resp = self.client.post(reverse('chunk_complete'), {
            'upload_id': upload_id,
            'filename': 'sample.txt',
            'total_chunks': 1,
            'expected_size': 9999,
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn('size', resp.json().get('error', '').lower())

    def test_chunked_uploaded_file_reopen_and_mime(self):
        self.client.login(username='testuser', password='password')
        upload_id = str(uuid.uuid4())
        self.client.post(reverse('chunk_upload'), {
            'upload_id': upload_id,
            'chunk_index': 0,
            'total_chunks': 1,
            'chunk_file': SimpleUploadedFile('avatar.png', b'\x89PNG\r\n\x1a\n'),
        })
        resp = self.client.post(reverse('chunk_complete'), {
            'upload_id': upload_id,
            'filename': 'avatar.png',
            'total_chunks': 1,
        })
        token = resp.json()['upload_token']
        assembled = ChunkedUploadManager.get_assembled_file(self.user.id, token)
        self.assertIsNotNone(assembled)
        self.assertEqual(assembled.content_type, 'image/png')
        # Test close and reopen
        assembled.close()
        self.assertTrue(assembled.file.closed)
        assembled.open('rb')
        self.assertFalse(assembled.file.closed)
        self.assertEqual(assembled.read(), b'\x89PNG\r\n\x1a\n')
        assembled.close()

    def test_problem_data_token_precedence_over_files(self):
        self.client.login(username='admin', password='password')
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, 'w') as zf:
            zf.writestr('1.in', '1 2\n')
            zf.writestr('1.out', '3\n')
        zip_bytes = zip_buf.getvalue()

        upload_id = str(uuid.uuid4())
        self.client.post(reverse('chunk_upload'), {
            'upload_id': upload_id,
            'chunk_index': 0,
            'total_chunks': 1,
            'chunk_file': SimpleUploadedFile('data.zip', zip_bytes),
        })
        complete_resp = self.client.post(reverse('chunk_complete'), {
            'upload_id': upload_id,
            'filename': 'data.zip',
            'total_chunks': 1,
        })
        token = complete_resp.json()['upload_token']

        # Send both token AND a dummy corrupted raw file in files
        dummy_file = SimpleUploadedFile('dummy.zip', b'corrupted_not_a_zip')
        resp = self.client.post(reverse('problem_data', args=[self.problem.code]), {
            'problem-data-zipfile': dummy_file,
            'problem-data-zipfile-token': token,
            'problem-data-grader': 'standard',
            'problem-data-checker': 'standard',
            'problem-data-checker_type': 'default',
            'cases-TOTAL_FORMS': '1',
            'cases-INITIAL_FORMS': '0',
            'cases-MIN_NUM_FORMS': '0',
            'cases-MAX_NUM_FORMS': '1',
            'cases-0-order': '1',
            'cases-0-type': 'C',
            'cases-0-input_file': '1.in',
            'cases-0-output_file': '1.out',
            'cases-0-points': '100',
        })
        # If token takes precedence, it extracts the valid ZIP and redirects 302
        self.assertEqual(resp.status_code, 302)

    def test_problem_data_existing_zipfile_with_empty_token(self):
        self.client.login(username='admin', password='password')

        # First upload a zip file with test cases
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, 'w') as zf:
            zf.writestr('test1/test.inp', '1 2\n')
            zf.writestr('test1/test.out', '3\n')
        zip_bytes = zip_buf.getvalue()

        upload_id = str(uuid.uuid4())
        self.client.post(reverse('chunk_upload'), {
            'upload_id': upload_id,
            'chunk_index': 0,
            'total_chunks': 1,
            'chunk_file': SimpleUploadedFile('test1.zip', zip_bytes),
        })
        complete_resp = self.client.post(reverse('chunk_complete'), {
            'upload_id': upload_id,
            'filename': 'test1.zip',
            'total_chunks': 1,
        })
        token = complete_resp.json()['upload_token']

        # Save problem data with the uploaded zip
        init_resp = self.client.post(reverse('problem_data', args=[self.problem.code]), {
            'problem-data-zipfile-token': token,
            'problem-data-grader': 'standard',
            'problem-data-checker': 'standard',
            'problem-data-checker_type': 'default',
            'cases-TOTAL_FORMS': '0',
            'cases-INITIAL_FORMS': '0',
            'cases-MIN_NUM_FORMS': '0',
            'cases-MAX_NUM_FORMS': '1',
        })
        self.assertEqual(init_resp.status_code, 302)
        problem_data = ProblemData.objects.get(problem=self.problem)
        self.assertTrue(bool(problem_data.zipfile))

        # Now submit the form AGAIN with empty token (e.g. user entering/editing testcases)
        resp = self.client.post(reverse('problem_data', args=[self.problem.code]), {
            'problem-data-zipfile-token': '',  # Empty token from hidden input
            'problem-data-grader': 'standard',
            'problem-data-checker': 'standard',
            'problem-data-checker_type': 'default',
            'cases-TOTAL_FORMS': '1',
            'cases-INITIAL_FORMS': '0',
            'cases-MIN_NUM_FORMS': '0',
            'cases-MAX_NUM_FORMS': '1',
            'cases-0-order': '1',
            'cases-0-type': 'C',
            'cases-0-input_file': 'test1/test.inp',
            'cases-0-output_file': 'test1/test.out',
            'cases-0-points': '100',
        })
        self.assertEqual(resp.status_code, 302)
        problem_data.refresh_from_db()
        self.assertEqual(problem_data.feedback, '')




