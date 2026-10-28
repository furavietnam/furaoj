from django.core.management.base import BaseCommand
from judge.utils.chunked_upload import ChunkedUploadManager


class Command(BaseCommand):
    help = 'Cleans up orphan chunk uploads and assembled files older than specified hours (default 24h).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--hours',
            type=int,
            default=24,
            help='Maximum age in hours before chunks are purged (default: 24).',
        )

    def handle(self, *args, **options):
        hours = options['hours']
        self.stdout.write(f'Cleaning up chunk uploads older than {hours} hours...')
        ChunkedUploadManager.cleanup_old_chunks(max_age_hours=hours)
        self.stdout.write(self.style.SUCCESS(f'Successfully purged chunks older than {hours} hours.'))
