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


