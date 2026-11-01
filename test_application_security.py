"""Exercise the real Discord callbacks with local I/O doubles and a temporary DB."""

import asyncio
import importlib
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord.ext import commands

sys.path.insert(0, str(Path(__file__).parent / "src"))
with patch.dict(os.environ, {
    "DISCORD_BOT_TOKEN": "offline-test-token",
    "INTERVIEW_CATEGORY_ID": "10",
    "OFFICER_ROLE_ID": "44",
    "ADMIN_ROLE_ID": "",
}), patch.object(commands.Bot, "run"):
    subject = importlib.import_module("bot")

from application_store import ApplicationRecord, ApplicationStore


def member(user_id, name="applicant", officer=False):
    user = MagicMock(spec=discord.Member)
    user.id, user.name, user.display_name = user_id, name, name
    user.mention, user.bot = f"<@{user_id}>", False
    user.roles = [SimpleNamespace(id=44)] if officer else []
    user.guild_permissions = SimpleNamespace(administrator=False, manage_channels=False)
    user.send = AsyncMock()
    return user


def not_found():
    return discord.NotFound(SimpleNamespace(status=404, reason="Not Found"),
                            {"code": 10003, "message": "Unknown Channel"})


class ApplicationSecurityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        log_context = patch.object(subject.logger, "disabled", True)
        log_context.start()
        self.addCleanup(log_context.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db_path = Path(self.directory.name) / "applications.sqlite3"
        self.store = ApplicationStore(self.db_path)
        self.addCleanup(lambda: self.store.close())
        for name, value in {
            "_application_store": self.store,
            "_registry_ready": set(),
            "_registry_locks": {},
            "ongoing_applications": {},
        }.items():
            context = patch.object(subject, name, value, create=True)
            context.start()
            self.addCleanup(context.stop)

        self.applicant = member(1001)
        self.officer = member(2001, "officer", officer=True)
        self.guild = MagicMock(spec=discord.Guild)
        self.guild.id, self.guild.name = 42, "Test Guild"
        self.guild.me = member(9999, "bot")
        self.guild.me.bot = True
        self.guild.default_role = MagicMock(spec=discord.Role)
        self.category = MagicMock(spec=discord.CategoryChannel)
        self.category.id = 10
        self.category.guild = self.guild
        self.channels = {10: self.category}
        self.guild.channels = [self.category]
        self.guild.get_channel.side_effect = self.channels.get
        self.guild.get_member.side_effect = lambda uid: self.applicant if uid == 1001 else None
        self.guild.get_role.return_value = MagicMock(spec=discord.Role)
        self.guild.fetch_channels.side_effect = lambda: list(self.channels.values())

        async def fetch(channel_id):
            if channel_id not in self.channels:
                raise not_found()
            return self.channels[channel_id]

        self.guild.fetch_channel.side_effect = fetch
        self.created = []

        async def create(**kwargs):
            channel = self.channel(3000 + len(self.created), kwargs["name"], kwargs["category"].id)
            self.created.append(channel)
            return channel

        self.guild.create_text_channel.side_effect = create
        context = patch.object(subject.bot, "process_commands", AsyncMock())
        context.start()
        self.addCleanup(context.stop)

    def channel(self, channel_id=2000, name="application-archive", category_id=10):
        channel = MagicMock(spec=discord.TextChannel)
        channel.id, channel.name, channel.category_id = channel_id, name, category_id
        channel.guild, channel.mention = self.guild, f"<#{channel_id}>"
        channel.category = self.category if category_id == 10 else SimpleNamespace(id=category_id)
        channel.messages = []

        async def history(**kwargs):
            messages = channel.messages if kwargs.get("oldest_first") else list(reversed(channel.messages))
            limit = kwargs.get("limit")
            for message in messages if limit is None else messages[:limit]:
                yield message

        async def send(*args, **kwargs):
            message = SimpleNamespace(
                id=5000 + len(channel.messages), author=self.guild.me, channel=channel,
                webhook_id=None, embeds=[kwargs["embed"]] if "embed" in kwargs else []
            )
            channel.messages.append(message)
            return message

        async def delete(**kwargs):
            self.channels.pop(channel_id, None)
            self.guild.channels = list(self.channels.values())

        channel.history.side_effect, channel.send.side_effect, channel.delete.side_effect = history, send, delete
        self.channels[channel_id] = channel
        self.guild.channels = list(self.channels.values())
        return channel

    def interaction(self, user=None, channel=None):
        return SimpleNamespace(
            user=user or self.applicant, guild=self.guild, channel=channel,
            response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock())
        )

    def message(self, content):
        return SimpleNamespace(author=self.applicant, channel=MagicMock(spec=discord.DMChannel), content=content)

    async def submit(self):
        await subject.ApplicationView().apply.callback(self.interaction())
        for index in range(len(subject.questions)):
            await subject.on_message(self.message(f"Ordinary application answer number {index}."))

    def register(self, channel, applicant_id=1001, message_id=5000):
        record = ApplicationRecord(self.guild.id, channel.id, applicant_id, message_id)
        self.store.add(record)
        return record

    def legacy_message(self, channel, applicant_id=1001, author=None, webhook_id=None):
        embed = discord.Embed(title="New Application from applicant")
        embed.add_field(name="Discord ID", value=str(applicant_id))
        embed.set_footer(text=f"Application submitted by applicant ({applicant_id})")
        message = SimpleNamespace(id=5000 + len(channel.messages), author=author or self.guild.me,
                                  channel=channel, webhook_id=webhook_id, embeds=[embed])
        channel.messages.append(message)
        return message

    def final_handler(self):
        handler = subject.ApplicationHandler(self.applicant, self.guild)
        handler.state = "active"
        handler.answers = ["A normal prior answer."] * (len(subject.questions) - 1)
        handler.current_question = len(subject.questions) - 1
        subject.ongoing_applications[self.applicant.id] = handler
        return handler

    async def test_both_staff_commands_reject_unrelated_prefix_channel(self):
        for command in (subject.approve_application, subject.reject_application):
            with self.subTest(command=command.name):
                channel = self.channel(category_id=99)
                interaction = self.interaction(self.officer, channel)
                with patch.object(subject, "schedule_channel_deletion", AsyncMock()) as schedule:
                    await command.callback(interaction, delete_time="1m")
                    await asyncio.sleep(0)
                    schedule.assert_not_called()
                self.assertFalse(any(call.kwargs.get("embed") for call in interaction.response.send_message.call_args_list))
                channel.send.assert_not_awaited()

    async def test_untracked_same_category_channels_are_rejected(self):
        channel = self.channel()
        for command in (subject.approve_application, subject.reject_application):
            with self.subTest(command=command.name):
                with patch.object(subject, "schedule_channel_deletion", AsyncMock()) as schedule:
                    await command.callback(self.interaction(self.officer, channel), delete_time="1m")
                    schedule.assert_not_called()
                channel.send.assert_not_awaited()

    async def test_registered_renamed_channel_preserves_both_staff_commands(self):
        channel = self.channel(name="staff-renamed-review")
        record = self.register(channel)
        for command, title in ((subject.approve_application, "✅ Application Approved"),
                               (subject.reject_application, "❌ Application Rejected")):
            with self.subTest(command=command.name):
                interaction = self.interaction(self.officer, channel)
                with patch.object(subject, "schedule_channel_deletion", AsyncMock()) as schedule:
                    await command.callback(interaction, delete_time="1m")
                    await asyncio.sleep(0)
                    schedule.assert_awaited_once_with(self.guild, record, 60)
                self.assertEqual(channel.send.call_args.kwargs["embed"].title, title)
                self.assertIn(str(self.applicant.id), channel.send.call_args.kwargs["embed"].description)
                self.assertIn("DM sent successfully", interaction.followup.send.call_args.args[0])
                self.assertIsNotNone(self.store.get(self.guild.id, channel.id))
        self.assertEqual(self.applicant.send.await_count, 2)

    async def test_unauthorized_staff_and_invalid_time_have_no_side_effects(self):
        channel = self.channel()
        self.register(channel)
        for command in (subject.approve_application, subject.reject_application):
            for user, delay in ((self.applicant, "1m"), (self.officer, "bad-time")):
                await command.callback(self.interaction(user, channel), delete_time=delay)
        channel.send.assert_not_awaited()
        self.guild.fetch_channel.assert_not_awaited()

    async def test_staff_dm_fallback_uses_registered_id(self):
        channel = self.channel()
        self.register(channel)
        self.guild.get_member.return_value = None
        self.guild.get_member.side_effect = None
        with patch.object(subject.bot, "fetch_user", AsyncMock(return_value=self.applicant)) as fetch:
            await subject.approve_application.callback(self.interaction(self.officer, channel))
            fetch.assert_awaited_once_with(1001)
        self.applicant.send.assert_awaited_once()

    async def test_staff_command_rejects_current_category_despite_stale_interaction(self):
        stale_channel = self.channel()
        self.register(stale_channel)
        current_channel = self.channel(category_id=99)
        for command in (subject.approve_application, subject.reject_application):
            await command.callback(self.interaction(self.officer, stale_channel), delete_time="1m")
        current_channel.send.assert_not_awaited()
        stale_channel.send.assert_not_awaited()

    async def test_delayed_deletion_rechecks_moved_or_changed_or_removed_record(self):
        for mutation in ("moved", "changed", "removed", "during_fetch"):
            with self.subTest(mutation=mutation):
                channel = self.channel(2100 + len(self.channels))
                record = self.register(channel)
                if mutation == "moved":
                    channel.category_id = 99
                elif mutation in ("changed", "removed"):
                    self.store.remove(record)
                    if mutation == "changed":
                        self.register(channel, applicant_id=7777)
                    # Do not let a startup migration substitute for a deleted record.
                    subject._registry_ready.add(self.guild.id)
                else:
                    fetch = self.guild.fetch_channel.side_effect

                    async def remove_during_fetch(channel_id):
                        self.store.remove(record)
                        return channel

                    self.guild.fetch_channel.side_effect = remove_during_fetch
                await subject.schedule_channel_deletion(self.guild, record, 0)
                channel.delete.assert_not_awaited()
                if mutation == "during_fetch":
                    self.guild.fetch_channel.side_effect = fetch

    async def test_valid_cleanup_deletes_once_and_allows_reapplication(self):
        channel = self.channel()
        record = self.register(channel)
        await subject.schedule_channel_deletion(self.guild, record, 0)
        channel.delete.assert_awaited_once()
        self.assertIsNone(self.store.get(self.guild.id, channel.id))
        await self.submit()
        self.assertEqual(len(self.created), 1)

    async def test_manual_deletion_allows_reapplication(self):
        channel = self.channel()
        self.register(channel)
        await channel.delete()
        await self.submit()
        self.assertEqual(len(self.created), 1)
        self.assertIsNone(self.store.get(self.guild.id, channel.id))

    async def test_unknown_access_failure_keeps_record_and_blocks_apply(self):
        channel = self.channel()
        record = self.register(channel)
        for error in (
            discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing Access"),
            discord.HTTPException(SimpleNamespace(status=500, reason="Error"), "Unavailable"),
            discord.NotFound(SimpleNamespace(status=404, reason="Not Found"),
                             {"code": 10004, "message": "Unknown Guild"}),
        ):
            with self.subTest(error=type(error).__name__):
                self.guild.fetch_channel.side_effect = error
                await subject.ApplicationView().apply.callback(self.interaction())
                self.assertEqual(self.store.get(self.guild.id, channel.id), record)
                self.assertNotIn(self.applicant.id, subject.ongoing_applications)
                self.guild.create_text_channel.assert_not_awaited()

    async def test_legacy_message_beyond_latest_fifty_is_adopted(self):
        channel = self.channel()
        original = self.legacy_message(channel)
        for _ in range(70):
            await channel.send(content="discussion")
        await subject.ensure_application_registry(self.guild)
        record = self.store.get(self.guild.id, channel.id)
        self.assertEqual(record, ApplicationRecord(self.guild.id, channel.id, 1001, original.id))
        self.applicant.name = "renamed"
        await subject.ApplicationView().apply.callback(self.interaction())
        self.guild.create_text_channel.assert_not_awaited()
        self.assertNotIn(self.applicant.id, subject.ongoing_applications)
        await subject.reject_application.callback(self.interaction(self.officer, channel))
        self.applicant.send.assert_awaited_once()

    async def test_forged_webhook_or_other_author_cannot_be_adopted(self):
        for author, webhook_id in ((self.applicant, None), (member(8888, "other-bot"), None),
                                   (self.guild.me, 999)):
            channel = self.channel(2200 + len(self.channels))
            self.legacy_message(channel, author=author, webhook_id=webhook_id)
            self.assertIsNone(await subject.legacy_application_record(channel))

    async def test_malformed_or_conflicting_legacy_identity_fails_closed(self):
        for invalid in ("duplicate-field", "different-footer", "nonnumeric", "two-originals"):
            with self.subTest(invalid=invalid):
                channel = self.channel(2300 + len(self.channels))
                message = self.legacy_message(channel)
                embed = message.embeds[0]
                if invalid == "duplicate-field":
                    embed.add_field(name="Discord ID", value="7777")
                elif invalid == "different-footer":
                    embed.set_footer(text="Application submitted by someone (7777)")
                elif invalid == "nonnumeric":
                    embed.set_field_at(0, name="Discord ID", value="-1001")
                else:
                    self.legacy_message(channel, applicant_id=7777)
                with self.assertRaises(subject.ApplicationRegistryError):
                    await subject.legacy_application_record(channel)
                self.assertIsNone(self.store.get(self.guild.id, channel.id))

    async def test_failed_migration_is_retryable_and_blocks_operations(self):
        channel = self.channel()
        self.legacy_message(channel)
        history = channel.history.side_effect
        channel.history.side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "Missing History Access")
        await subject.ApplicationView().apply.callback(self.interaction())
        self.assertNotIn(self.guild.id, subject._registry_ready)
        self.assertNotIn(self.applicant.id, subject.ongoing_applications)
        self.applicant.send.assert_not_awaited()
        channel.history.side_effect = history
        await subject.ensure_application_registry(self.guild)
        self.assertIsNotNone(self.store.get(self.guild.id, channel.id))

    async def test_existing_legacy_duplicates_all_remain_tracked(self):
        first, second = self.channel(2400), self.channel(2401)
        self.legacy_message(first)
        self.legacy_message(second)
        await subject.ensure_application_registry(self.guild)
        self.assertEqual(len(self.store.for_applicant(self.guild.id, 1001)), 2)
        await first.delete()
        await subject.ApplicationView().apply.callback(self.interaction())
        self.assertEqual(len(self.store.for_applicant(self.guild.id, 1001)), 1)
        self.assertNotIn(1001, subject.ongoing_applications)

    async def test_moved_legacy_channel_blocks_reapplication_but_not_authorized_for_decisions(self):
        channel = self.channel(category_id=99)
        self.legacy_message(channel)
        self.applicant.name = "renamedapplicant"
        await self.submit()
        self.assertEqual(len(self.created), 0)
        self.assertIsNotNone(self.store.get(self.guild.id, channel.id))
        for command in (subject.approve_application, subject.reject_application):
            await command.callback(self.interaction(self.officer, channel), delete_time="1m")
        channel.send.assert_not_awaited()
        channel.delete.assert_not_awaited()

    async def test_concurrent_final_answers_create_one_application(self):
        handler = self.final_handler()
        entered, release = asyncio.Event(), asyncio.Event()
        create = self.guild.create_text_channel.side_effect

        async def held_create(**kwargs):
            entered.set()
            await release.wait()
            return await create(**kwargs)

        self.guild.create_text_channel.side_effect = held_create
        first = asyncio.create_task(subject.on_message(self.message("First final answer.")))
        await asyncio.wait_for(entered.wait(), 2)
        second = asyncio.create_task(subject.on_message(self.message("Second final answer.")))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, second)
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self.created[0].send.await_count, 1)
        self.assertEqual(len(self.store.for_applicant(self.guild.id, 1001)), 1)
        self.assertEqual(handler.state, "completed")

    async def test_cancellation_wins_before_final_answer_and_preserves_new_handler(self):
        handler = self.final_handler()
        entered, release = asyncio.Event(), asyncio.Event()

        async def held_dm(**kwargs):
            entered.set()
            await release.wait()

        self.applicant.send.side_effect = held_dm
        cancellation = asyncio.create_task(handler.process_answer(self.message("cancel")))
        await asyncio.wait_for(entered.wait(), 2)
        replacement = subject.ApplicationHandler(self.applicant, self.guild)
        subject.ongoing_applications[1001] = replacement
        stale_answer = asyncio.create_task(handler.process_answer(self.message("Late final answer.")))
        release.set()
        await asyncio.gather(cancellation, stale_answer)
        self.assertIs(subject.ongoing_applications[1001], replacement)
        self.assertEqual(handler.state, "cancelled")
        self.guild.create_text_channel.assert_not_awaited()

    async def test_finalization_wins_before_cancellation(self):
        handler = self.final_handler()
        entered, release = asyncio.Event(), asyncio.Event()
        create = self.guild.create_text_channel.side_effect

        async def held_create(**kwargs):
            entered.set()
            await release.wait()
            return await create(**kwargs)

        self.guild.create_text_channel.side_effect = held_create
        completion = asyncio.create_task(handler.process_answer(self.message("Final answer.")))
        await asyncio.wait_for(entered.wait(), 2)
        cancellation = asyncio.create_task(handler.process_answer(self.message("cancel")))
        release.set()
        await asyncio.gather(completion, cancellation)
        self.assertEqual(len(self.created), 1)
        self.assertEqual(handler.state, "completed")
        self.assertFalse(any(call.kwargs.get("embed") and call.kwargs["embed"].title == "Application Cancelled"
                             for call in self.applicant.send.call_args_list))

    async def test_stale_completion_does_not_clear_replacement(self):
        handler = self.final_handler()
        replacement = subject.ApplicationHandler(self.applicant, self.guild)
        lookup = subject.existing_application_channel

        async def replace_during_lookup(*args):
            subject.ongoing_applications[1001] = replacement
            return await lookup(*args)

        with patch.object(subject, "existing_application_channel", replace_during_lookup):
            await handler.complete_application()
        self.assertIs(subject.ongoing_applications[1001], replacement)
        self.guild.create_text_channel.assert_not_awaited()

    async def test_failed_old_start_does_not_clear_replacement(self):
        old = subject.ApplicationHandler(self.applicant, self.guild)
        replacement = subject.ApplicationHandler(self.applicant, self.guild)
        subject.ongoing_applications[1001] = old

        async def replace_then_fail(**kwargs):
            subject.ongoing_applications[1001] = replacement
            raise discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "DM disabled")

        self.applicant.send.side_effect = replace_then_fail
        self.assertFalse(await old.start_application())
        self.assertIs(subject.ongoing_applications[1001], replacement)

    async def test_concurrent_apply_starts_one_session(self):
        first, second = self.interaction(), self.interaction()
        await asyncio.gather(subject.ApplicationView().apply.callback(first),
                             subject.ApplicationView().apply.callback(second))
        self.applicant.send.assert_awaited_once()
        self.assertEqual(len(subject.ongoing_applications), 1)

    async def test_completion_rechecks_existing_applicant_identity(self):
        handler = self.final_handler()
        self.register(self.channel())
        await handler.complete_application()
        self.guild.create_text_channel.assert_not_awaited()

    async def test_partial_publication_failure_retains_duplicate_guard(self):
        create = self.guild.create_text_channel.side_effect

        async def broken_publication(**kwargs):
            channel = await create(**kwargs)
            channel.send.side_effect = RuntimeError("simulated send failure")
            return channel

        self.guild.create_text_channel.side_effect = broken_publication
        await self.submit()
        record = self.store.for_applicant(self.guild.id, 1001)[0]
        self.assertIsNone(record.message_id)
        self.applicant.name = "changed"
        await self.submit()
        self.assertEqual(len(self.created), 1)
        with self.assertRaises(subject.ApplicationRegistryError):
            await subject.verified_application_channel(self.guild, record.channel_id)

    async def test_database_failure_blocks_apply_and_decision(self):
        channel = self.channel()
        with patch.object(subject, "get_application_store", side_effect=sqlite3.OperationalError("unavailable")):
            await subject.ApplicationView().apply.callback(self.interaction())
            await subject.approve_application.callback(self.interaction(self.officer, channel))
        self.guild.create_text_channel.assert_not_awaited()
        channel.send.assert_not_awaited()
        self.assertNotIn(1001, subject.ongoing_applications)

    async def test_failed_record_write_rolls_back_to_durable_reservation(self):
        self.store.connection.execute("""
            CREATE TRIGGER fail_record BEFORE INSERT ON applications
            BEGIN SELECT RAISE(ABORT, 'simulated disk write failure'); END
        """)
        await self.submit()
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self.store.for_applicant(self.guild.id, 1001), [])
        self.assertTrue(self.store.has_reservation(self.guild.id, 1001))
        self.store.connection.execute("DROP TRIGGER fail_record")
        self.store.close()
        self.store = ApplicationStore(self.db_path)
        subject._application_store = self.store
        subject._registry_ready.clear()
        subject._registry_locks.clear()
        self.applicant.name = "renamed-after-failure"
        await self.submit()
        self.assertEqual(len(self.created), 1)
        self.assertTrue(self.store.has_reservation(self.guild.id, 1001))

    async def test_failed_reservation_write_creates_no_channel(self):
        self.store.connection.execute("""
            CREATE TRIGGER fail_reservation BEFORE INSERT ON application_reservations
            BEGIN SELECT RAISE(ABORT, 'simulated disk write failure'); END
        """)
        await self.submit()
        self.guild.create_text_channel.assert_not_awaited()

    async def test_uncertain_create_response_does_not_allow_another_channel(self):
        create = self.guild.create_text_channel.side_effect

        async def created_but_timed_out(**kwargs):
            await create(**kwargs)
            raise asyncio.TimeoutError("response lost")

        self.guild.create_text_channel.side_effect = created_but_timed_out
        await self.submit()
        self.assertTrue(self.store.has_reservation(self.guild.id, 1001))
        self.guild.create_text_channel.side_effect = create
        await self.submit()
        self.assertEqual(len(self.created), 1)

    async def test_definite_creation_refusal_allows_retry(self):
        create = self.guild.create_text_channel.side_effect
        self.guild.create_text_channel.side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "Cannot create channel")
        await self.submit()
        self.assertFalse(self.store.has_reservation(self.guild.id, 1001))
        self.guild.create_text_channel.side_effect = create
        await self.submit()
        self.assertEqual(len(self.created), 1)

    async def test_username_change_cannot_bypass_retained_application(self):
        await self.submit()
        self.applicant.name = "renamedapplicant"
        await self.submit()
        self.assertEqual(len(self.created), 1)

    async def test_restart_and_renames_preserve_duplicate_guard(self):
        await self.submit()
        self.created[0].name = "renamed-channel"
        self.applicant.name = "renamedapplicant"
        self.store.close()
        self.store = ApplicationStore(self.db_path)
        subject._application_store = self.store
        subject._registry_ready.clear()
        subject._registry_locks.clear()
        subject.ongoing_applications.clear()
        await self.submit()
        self.assertEqual(len(self.created), 1)

    async def test_distinct_user_with_same_name_can_apply(self):
        await self.submit()
        self.applicant = member(1002, name=self.applicant.name)
        await self.submit()
        self.assertEqual(len(self.created), 2)
        self.assertEqual(self.store.for_applicant(self.guild.id, 1002)[0].applicant_id, 1002)

    async def test_other_guild_record_does_not_block_applicant(self):
        self.store.add(ApplicationRecord(43, 9000, 1001, 9001))
        await self.submit()
        self.assertEqual(len(self.created), 1)


if __name__ == "__main__":
    unittest.main()
