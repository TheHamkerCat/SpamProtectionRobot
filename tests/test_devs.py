import unittest
from unittest import mock

from spr.modules import devs
from spr.modules.devs import (eval_command, run_shell_line, runtime_callback,
                              shell_command)
from tests.fakes import (SUDO_ID, USER_ID, FakeCallbackEvent, FakeClient,
                         FakeMessageEvent, make_user, setup_bot_username)


class ShellTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        setup_bot_username()
        self.client = FakeClient()

    async def test_output_and_errors_are_captured(self):
        self.assertEqual(await run_shell_line("echo 'hello world'"), "hello world")
        output = await run_shell_line("ls /does-not-exist")
        self.assertIn("No such file", output)

    async def test_bad_input(self):
        self.assertIn("Can't parse", await run_shell_line("echo 'unclosed"))
        self.assertIn("Can't run", await run_shell_line("no-such-binary-xyz"))

    async def test_timeout_kills_the_process(self):
        with mock.patch.object(devs, "SHELL_TIMEOUT_SECONDS", 0.5):
            self.assertIn("Stopped after", await run_shell_line("sleep 5"))

    async def test_long_output_is_sent_as_file(self):
        event = FakeMessageEvent(self.client, make_user(SUDO_ID), "/sh seq 1 3000", is_private=True)
        await shell_command(event)
        text, kwargs = event.responses[0]
        self.assertEqual(kwargs["file"].name, "output.txt")
        self.assertIn(b"3000", kwargs["file"].getvalue())

    async def test_multi_line_and_escaping(self):
        event = FakeMessageEvent(self.client, make_user(SUDO_ID), "/sh echo '<b>'\necho two", is_private=True)
        await shell_command(event)
        text = event.reply_texts()[0]
        self.assertIn("$ echo &#x27;&lt;b&gt;&#x27;\n&lt;b&gt;", text)
        self.assertIn("$ echo two\ntwo", text)

    def test_only_trusted_sudo_users(self):
        sudo = FakeMessageEvent(self.client, make_user(SUDO_ID), "/sh ls")
        self.assertTrue(devs.is_trusted_sudo(sudo))
        sudo.fwd_from = object()
        self.assertFalse(devs.is_trusted_sudo(sudo))
        self.assertFalse(devs.is_trusted_sudo(FakeMessageEvent(self.client, make_user(USER_ID), "/sh ls")))


class EvalTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        setup_bot_username()
        self.client = FakeClient()

    async def test_output_is_escaped(self):
        event = FakeMessageEvent(self.client, make_user(SUDO_ID), "/eval print('<i>x</i>')")
        await eval_command(event)
        text, kwargs = event.replies[0]
        self.assertIn("&lt;i&gt;x&lt;/i&gt;", text)
        self.assertIn("buttons", kwargs)

    async def test_exception_is_shown(self):
        event = FakeMessageEvent(self.client, make_user(SUDO_ID), "/eval 1/0")
        await eval_command(event)
        self.assertIn("ZeroDivisionError", event.reply_texts()[0])

    async def test_crafted_runtime_data(self):
        for data in (b"runtime ", b"runtime \xff\xfe"):
            event = FakeCallbackEvent(self.client, USER_ID, data)
            await runtime_callback(event)
            self.assertEqual(len(event.answers), 1)
