import asyncio
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from modules import text_struct as struct


def message(mid, **kwargs):
    values = dict(id=mid,chat=NS(id=42),text=None,caption=None,media=None,message_thread_id=None,
                  reply_to_message=None,edit_text=AsyncMock(),delete=AsyncMock())
    values.update(kwargs)
    return NS(**values)


class StructSourcesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)

    def test_number_and_multiline_modifier_are_separate(self):
        self.assertEqual(struct._parse_batch_modifier('2 расшифруй два аудио\nс отцом и матерью'),
                         (2,'расшифруй два аудио\nс отцом и матерью'))
        self.assertEqual(struct._parse_batch_modifier('100'),(100,''))
        self.assertEqual(struct._parse_batch_modifier('0 задача'),(0,'задача'))
        self.assertIsNone(struct._parse_batch_modifier('обычный текст'))

    async def test_history_includes_all_media_and_keeps_chronological_order(self):
        command=message(20)
        sources=[message(21,text='newer'),message(19,document=NS(file_name='one.mp3')),message(18,photo=NS()),
                 message(18,photo=NS()),message(17,media='poll'),message(16),message(15,text='old')]
        async def history(*args,**kwargs):
            self.assertEqual(kwargs['offset_id'],20)
            for item in sources:yield item
        result=await struct._collect_batch_messages(NS(get_chat_history=history),command,3)
        self.assertEqual([item.id for item in result],[17,18,19])

    async def test_history_does_not_take_messages_from_another_topic(self):
        async def history(*args,**kwargs):
            yield message(9,text='other topic',message_thread_id=7)
            yield message(8,text='same topic',message_thread_id=3)
        result=await struct._collect_batch_messages(NS(get_chat_history=history),message(10,message_thread_id=3),1)
        self.assertEqual([item.id for item in result],[8])

    async def test_mp3_document_is_downloaded_and_transcribed_as_audio(self):
        item=message(1,document=NS(file_name='dialogue.mp3',mime_type='application/octet-stream'))
        async def download(client,msg,file_name,**kwargs):
            self.assertTrue(file_name.endswith('.mp3'))
            target=self.folder/'audio.mp3';target.write_bytes(b'actual audio payload');return str(target)
        async def transcribe(path):
            self.assertEqual(Path(path).read_bytes(),b'actual audio payload');return 'complete dialogue'
        with patch.object(struct,'download_media_checked',download),patch.object(struct,'transcribe_media',AsyncMock(side_effect=transcribe)) as speech:
            self.assertEqual(await struct._read_struct_media(None,item),'complete dialogue')
            speech.assert_awaited_once()
        self.assertFalse((self.folder/'audio.mp3').exists())

    async def test_photo_attaches_file_to_model_instead_of_only_sending_caption(self):
        target=self.folder/'photo.jpg';target.write_bytes(b'image data')
        async def generate(prompt,**kwargs):
            self.assertEqual(Path(kwargs['media_path']).read_bytes(),b'image data')
            return 'full readable text'
        with patch.object(struct,'download_media_checked',AsyncMock(return_value=str(target))),patch.object(struct,'_generate_struct_response',AsyncMock(side_effect=generate)):
            self.assertEqual(await struct._read_struct_media(None,message(1,photo=NS())),'full readable text')
        self.assertFalse(target.exists())

    async def test_plain_document_reads_its_actual_contents(self):
        target=self.folder/'notes.txt';target.write_text('текст документа',encoding='utf-8')
        with patch.object(struct,'download_media_checked',AsyncMock(return_value=str(target))),patch.object(struct,'_generate_struct_response',AsyncMock()) as model:
            result=await struct._read_struct_media(None,message(1,document=NS(file_name='notes.txt',mime_type='text/plain')))
            self.assertEqual(result,'текст документа');model.assert_not_awaited()

    def test_spreadsheet_resolves_shared_strings_in_cells(self):
        target=self.folder/'table.xlsx'
        with zipfile.ZipFile(target,'w') as archive:
            archive.writestr('xl/sharedStrings.xml','<sst xmlns="urn:test"><si><t>Egypt</t></si></sst>')
            archive.writestr('xl/worksheets/sheet1.xml','<worksheet xmlns="urn:test"><sheetData><row><c r="A1" t="s"><v>0</v></c><c r="B1"><v>12</v></c></row></sheetData></worksheet>')
        result=struct._document_text(target)
        self.assertIn('A1: Egypt',result);self.assertIn('B1: 12',result)

    async def test_unsupported_file_is_explicit_and_temporary_file_is_removed(self):
        target=self.folder/'binary.zip';target.write_bytes(b'not readable as media')
        with patch.object(struct,'download_media_checked',AsyncMock(return_value=str(target))),patch.object(struct,'_generate_struct_response',AsyncMock()) as model:
            with self.assertRaises(struct.StructSourceError):
                await struct._read_struct_media(None,message(1,document=NS(file_name='binary.zip',mime_type='application/zip')))
            model.assert_not_awaited()
        self.assertFalse(target.exists())

    async def test_caption_does_not_hide_a_failed_attachment(self):
        sources=[message(1,caption='caption alone is incomplete',document=NS(file_name='x.mp3'))]
        with patch.object(struct,'_read_struct_media',AsyncMock(return_value='')):
            with self.assertRaises(struct.StructSourceError):
                await struct._build_batch_source_text(None,message(5),sources)

    async def test_oversized_batch_is_rejected_without_truncating_sources(self):
        with patch.object(struct,'BATCH_TEXT_LIMIT',5):
            with self.assertRaises(struct.StructSourceError):
                await struct._build_batch_source_text(None,message(5),[message(1,text='long text')])

    async def test_progress_identifies_each_attachment_before_its_read_starts(self):
        events=[]
        async def progress(index,total):events.append(('progress',index,total))
        async def read(client,item,**kwargs):events.append(('read',item.id));return 'full transcript'
        with patch.object(struct,'_read_struct_media',read):
            await struct._build_batch_source_text(None,message(5),[message(1,audio=NS()),message(2,audio=NS())],progress_cb=progress)
        self.assertEqual(events,[('progress',1,2),('read',1),('progress',2,2),('read',2)])

    async def run_command(self,command,sources,read_effect=None):
        app=NS()
        def on_message(*args,**kwargs):
            def decorator(handler):app.handler=handler;return handler
            return decorator
        app.on_message=on_message
        async def history(*args,**kwargs):
            for item in reversed(sources):yield item
        app.get_chat_history=history
        async def read(client,item,**kwargs):return f'FULL AUDIO {item.id}'
        with patch.object(struct,'_get_cfg',AsyncMock(return_value={'is_active':True,'command':'.fix','prompt':'default writer style'})), \
             patch.object(struct,'recover_cleanup',AsyncMock()),patch.object(struct,'simulate_typing',AsyncMock()), \
             patch.object(struct,'_read_struct_media',AsyncMock(side_effect=read_effect or read)) as media, \
             patch.object(struct,'_generate_struct_response',AsyncMock(return_value='complete result')) as generate, \
             patch.object(struct,'_apply_edit',AsyncMock(return_value=True)) as edit, \
             patch.object(struct,'_delete_source_messages',AsyncMock()) as delete:
            struct.register_userbot(app)
            await app.handler(app,command)
            if struct._cleanup_task:
                await struct._cleanup_task
            return media,generate,edit,delete

    async def test_exact_user_command_processes_both_mp3_documents_and_extra_prompt(self):
        sources=[message(1,document=NS(file_name='father.mp3')),message(2,document=NS(file_name='mother.mp3'))]
        media,generate,edit,delete=await self.run_command(message(5,text='.fix 2 расшифруй два аудио диалога с отцом и матерью по поводу египта.'),sources)
        self.assertEqual(media.await_count,2)
        source=generate.await_args.args[0]
        self.assertLess(source.index('FULL AUDIO 1'),source.index('FULL AUDIO 2'))
        prompt=generate.await_args.kwargs['custom_prompt']
        self.assertTrue(prompt.endswith('расшифруй два аудио диалога с отцом и матерью по поводу египта.'))
        self.assertIn('preference to default rewriting style',prompt)
        delete.assert_awaited_once_with(sources)

    async def test_missing_second_message_does_not_generate_or_delete(self):
        media,generate,edit,delete=await self.run_command(message(5,text='.fix 2 task'),[message(1,text='only one')])
        generate.assert_not_awaited();media.assert_not_awaited();delete.assert_not_awaited()
        self.assertIsNone(edit.await_args.args[2])

    async def test_failed_second_attachment_does_not_generate_or_delete(self):
        media,generate,edit,delete=await self.run_command(message(5,text='.fix 2 task'),
            [message(1,audio=NS()),message(2,document=NS(file_name='x.mp3'))],['first complete transcript',ValueError('failed')])
        self.assertEqual(media.await_count,2)
        generate.assert_not_awaited();delete.assert_not_awaited()
        self.assertIsNone(edit.await_args.args[2])
