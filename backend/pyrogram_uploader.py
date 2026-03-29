"""
Pyrogram-based uploader/downloader for large files (up to 2 GB).
Uses Telegram MTProto API via Pyrogram.
Reports progress via a callback function.
"""

import os
import asyncio
from pyrogram import Client
from pyrogram.types import Message


class PyrogramUploader:
    def __init__(self, api_id: str, api_hash: str, bot_token: str):
        self.api_id = int(api_id)
        self.api_hash = api_hash
        self.bot_token = bot_token
        self._client: Client | None = None

    async def _get_client(self) -> Client:
        if self._client and self._client.is_connected:
            return self._client
        self._client = Client(
            name="telegallery_bot",
            api_id=self.api_id,
            api_hash=self.api_hash,
            bot_token=self.bot_token,
            in_memory=True,
        )
        await self._client.start()
        return self._client

    async def send_file(
        self,
        chat_id: str,
        file_path: str,
        file_type: str = 'photo',
        progress_callback=None,
    ) -> dict:
        """Upload a large file via Pyrogram (supports up to 2 GB)."""
        client = await self._get_client()

        # Always send as document to preserve quality
        msg: Message = await client.send_document(
            chat_id=int(chat_id),
            document=file_path,
            force_document=True,
            progress=progress_callback,
        )

        result = {
            'message_id': msg.id,
            'document': None,
            'photo': None,
        }

        if msg.document:
            result['document'] = {
                'file_id': msg.document.file_id,
                'file_name': msg.document.file_name or os.path.basename(file_path),
                'file_size': msg.document.file_size,
                'mime_type': msg.document.mime_type,
            }
            if msg.document.thumbs:
                result['document']['thumbnail'] = {
                    'file_id': msg.document.thumbs[0].file_id,
                }

        # For photos, also send as photo to get thumbnail
        if file_type == 'photo':
            try:
                thumb_msg = await client.send_photo(
                    chat_id=int(chat_id),
                    photo=file_path,
                )
                if thumb_msg.photo:
                    sizes = thumb_msg.photo.thumbs or []
                    # Pyrogram photo object
                    result['photo'] = {
                        'file_id': thumb_msg.photo.file_id,
                        'width': thumb_msg.photo.width or 0,
                        'height': thumb_msg.photo.height or 0,
                        'thumbs': [{'file_id': s.file_id} for s in sizes] if sizes else [],
                    }
            except Exception:
                pass  # Thumbnail is optional

        return result

    async def download_file(self, file_id: str, dest_path: str, progress_callback=None) -> str:
        """Download a file from Telegram via Pyrogram (supports large files)."""
        client = await self._get_client()
        path = await client.download_media(
            file_id,
            file_name=dest_path,
            progress=progress_callback,
        )
        return path or dest_path

    async def disconnect(self):
        if self._client and self._client.is_connected:
            await self._client.stop()
            self._client = None
