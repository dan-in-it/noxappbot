"""Restart, review/edit, role assignment and durable cleanup behavior."""

import asyncio
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from test_application_security import ApplicationTestCase, ApplicationRecord, ApplicationStore, member, subject


class ApplicationWorkflowTests(ApplicationTestCase):
    def restart(self):
        self.store.close()
        self.store = ApplicationStore(self.db_path)
        subject._application_store = self.store
        subject.ongoing_applications.clear()
        subject._registry_ready.clear()
        subject._registry_locks.clear()
        subject._decision_locks.clear()

    async def answer_form(self):
        await subject.ApplicationView().apply.callback(self.interaction())
        for index in range(len(subject.questions)):
            await subject.on_message(self.message(f"My answer to question {index + 1}."))
        return subject.ongoing_applications[self.applicant.id]

    def long_answer_handler(self):
        handler = self.final_handler()
        handler.answers = [(f"Answer {index}. " + "A detailed response with varied words. " * 30)[:800]
                           for index in range(len(subject.questions))]
        handler.save_progress()
        return handler

    def fail_send_once(self, call_number, error=None):
        create = self.guild.create_text_channel.side_effect

        async def create_with_flaky_send(**kwargs):
            channel = await create(**kwargs)
            send, calls = channel.send.side_effect, []

            async def flaky_send(*args, **kwargs):
                calls.append(kwargs)
                if len(calls) == call_number:
                    raise error or discord.HTTPException(
                        SimpleNamespace(status=500, reason="Server Error"), "Temporary failure")
                return await send(*args, **kwargs)

            channel.send.side_effect = flaky_send
            return channel

        self.guild.create_text_channel.side_effect = create_with_flaky_send

    def posted_answers(self, channel):
        return [field.value for message in channel.messages for embed in message.embeds
                for field in embed.fields if field.name.startswith("Q")]

    def rejection_with_cleanup(self, seconds):
        channel = self.channel()
        record = self.register(channel)
        self.store.begin_decision(record, "rejecting", self.officer.id, "Saved decision", seconds)
        self.store.finish_decision(record, "rejected")
        return channel, record

    async def test_existing_database_is_migrated_without_losing_identity(self):
        path = self.db_path.with_name("legacy.sqlite3")
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE applications (guild_id INTEGER, channel_id INTEGER, "
                               "applicant_id INTEGER, message_id INTEGER, PRIMARY KEY (guild_id, channel_id))")
            connection.execute("INSERT INTO applications VALUES (42, 2000, 1001, 5000)")
        connection.close()
        store = ApplicationStore(path)
        self.addCleanup(store.close)
        record = ApplicationRecord(42, 2000, 1001, 5000)
        self.assertEqual(store.get(42, 2000), record)
        self.assertEqual(store.workflow(record)["status"], "pending")
        self.assertEqual(store.for_applicant(42, 1001), [record])

    async def test_saved_draft_resumes_from_dm_with_original_question_snapshot(self):
        await subject.ApplicationView().apply.callback(self.interaction())
        await subject.on_message(self.message("Thorin Stoneguard"))
        original_questions = list(subject.questions)
        self.restart()
        with patch.object(subject, "questions", ["A changed form for future applicants"]):
            await subject.on_message(self.message("resume"))
        handler = subject.ongoing_applications[1001]
        self.assertEqual(handler.answers, ["Thorin Stoneguard"])
        self.assertEqual(handler.current_question, 1)
        self.assertEqual(handler.questions, original_questions)
        self.assertIn("Question 2/", self.applicant.send.call_args.kwargs["embed"].title)
        self.guild.create_text_channel.assert_not_awaited()

    async def test_apply_button_restores_saved_draft(self):
        await subject.ApplicationView().apply.callback(self.interaction())
        await subject.on_message(self.message("My first answer."))
        self.restart()
        await subject.ApplicationView().apply.callback(self.interaction())
        self.assertEqual(subject.ongoing_applications[1001].answers, ["My first answer."])
        self.assertIn("Question 2/", self.applicant.send.call_args.kwargs["embed"].title)

    async def test_review_requires_explicit_submission_and_survives_restart(self):
        handler = await self.answer_form()
        self.assertEqual(handler.state, "review")
        self.guild.create_text_channel.assert_not_awaited()
        self.restart()
        await subject.on_message(self.message("Another unsolicited answer"))
        self.guild.create_text_channel.assert_not_awaited()
        await subject.on_message(self.message("submit"))
        self.assertEqual(len(self.created), 1)
        self.assertIsNone(self.store.get_draft(1001))
        record = self.store.for_applicant(42, 1001)[0]
        self.assertEqual(self.store.workflow(record)["answers"], handler.answers)

    async def test_back_and_edit_preserve_other_answers_across_restart(self):
        handler = await self.answer_form()
        original = list(handler.answers)
        await subject.on_message(self.message("back"))
        self.assertEqual(handler.current_question, len(subject.questions) - 1)
        await subject.on_message(self.message("Updated final answer."))
        await subject.on_message(self.message("edit 2"))
        self.restart()
        await subject.on_message(self.message("I plan to play a protection warrior tank."))
        restored = subject.ongoing_applications[1001]
        self.assertEqual(restored.state, "review")
        self.assertEqual(restored.answers[1], "I plan to play a protection warrior tank.")
        self.assertEqual(restored.answers[-1], "Updated final answer.")
        self.assertEqual(restored.answers[2:-1], original[2:-1])
        self.guild.create_text_channel.assert_not_awaited()

    async def test_back_during_draft_returns_to_next_unanswered_question(self):
        await subject.ApplicationView().apply.callback(self.interaction())
        await subject.on_message(self.message("Original first answer."))
        await subject.on_message(self.message("back"))
        await subject.on_message(self.message("Corrected first answer."))
        handler = subject.ongoing_applications[1001]
        self.assertEqual(handler.current_question, 1)
        self.assertEqual(handler.answers, ["Corrected first answer."])

    async def test_incomplete_submission_empty_answers_and_invalid_edits_do_not_advance(self):
        await subject.ApplicationView().apply.callback(self.interaction())
        for content in ("submit", "", "  ", "back", "edit", "edit 0", "edit -1", "edit 99", "edit abc"):
            await subject.on_message(self.message(content))
        handler = subject.ongoing_applications[1001]
        self.assertEqual(handler.current_question, 0)
        self.assertEqual(handler.answers, [])
        self.guild.create_text_channel.assert_not_awaited()

    async def test_cancellation_removes_persistent_draft(self):
        await self.answer_form()
        await subject.on_message(self.message("cancel"))
        self.restart()
        self.assertIsNone(self.store.get_draft(1001))
        await subject.ApplicationView().apply.callback(self.interaction())
        self.assertEqual(subject.ongoing_applications[1001].answers, [])

    async def test_long_answer_prompt_survives_restart_and_does_not_leak_to_next_question(self):
        await subject.ApplicationView().apply.callback(self.interaction())
        await subject.on_message(self.message("Long but meaningful explanation. " * 40))
        self.restart()
        await subject.on_message(self.message("proceed"))
        handler = subject.ongoing_applications[1001]
        self.assertLessEqual(len(handler.answers[0]), 800)
        await subject.on_message(self.message("proceed"))
        self.assertEqual(handler.answers[1], "proceed")

    async def test_draft_write_failure_keeps_last_saved_question(self):
        await subject.ApplicationView().apply.callback(self.interaction())
        with patch.object(self.store, "save_draft", side_effect=sqlite3.OperationalError("disk full")):
            await subject.on_message(self.message("This answer could not be saved."))
        handler = subject.ongoing_applications[1001]
        self.assertEqual(handler.answers, [])
        self.assertEqual(handler.current_question, 0)
        self.assertEqual(self.store.get_draft(1001)["answers"], [])

    async def test_all_answers_are_saved_and_published_when_one_embed_is_too_small(self):
        handler = self.final_handler()
        handler.answers = [(f"Answer {index}. " + "A detailed response with varied words. " * 30)[:800]
                           for index in range(len(subject.questions))]
        handler.save_progress()
        await handler.send_review()
        await subject.on_message(self.message("submit"))
        channel = self.created[0]
        self.assertGreater(len(channel.messages), 1)
        answers = [field.value for message in channel.messages for embed in message.embeds
                   for field in embed.fields if field.name.startswith("Q")]
        self.assertEqual(answers, handler.answers)
        self.assertTrue(all(len(embed) <= 6000 for message in channel.messages for embed in message.embeds))
        record = self.store.for_applicant(42, 1001)[0]
        self.assertEqual(self.store.workflow(record)["answers"], handler.answers)
        self.assertEqual(await subject.legacy_application_record(channel), record)

    async def test_approval_grants_both_exact_roles_and_saves_reviewer_and_onboarding(self):
        channel = self.channel()
        record = self.register(channel)
        with patch.object(subject, "ONBOARDING_LINKS", {
            "Guild rules": "https://discord.com/channels/42/11",
            "Raid schedule": "https://discord.com/channels/42/12",
            "Introductions": "https://discord.com/channels/42/13",
        }):
            await subject.approve_application.callback(self.interaction(self.officer, channel), "Welcome aboard!")
        self.guild.fetch_member.assert_awaited_once_with(1001)
        call = self.applicant.add_roles.call_args
        self.assertEqual([role.id for role in call.args], [616354179522363400, 1548423178794307745])
        self.assertTrue(call.kwargs["atomic"])
        workflow = self.store.workflow(record)
        self.assertEqual(workflow["status"], "approved")
        self.assertEqual(workflow["reviewer_id"], self.officer.id)
        self.assertEqual(workflow["decision_note"], "Welcome aboard!")
        self.assertIsNotNone(workflow["decided_at"])
        next_steps = self.applicant.send.call_args.kwargs["embed"].fields[-1].value
        for channel_id in (11, 12, 13):
            self.assertIn(f"https://discord.com/channels/42/{channel_id}", next_steps)
        self.assertIn("in-game guild invitation", next_steps)

    async def test_role_configuration_failures_do_not_send_approval_or_schedule_deletion(self):
        for failure in ("missing", "permission", "hierarchy", "left-server"):
            with self.subTest(failure=failure):
                channel = self.channel(2200 + len(self.channels))
                record = self.register(channel)
                self.guild.me.guild_permissions.manage_roles = failure != "permission"
                self.approval_roles[0].is_assignable.return_value = failure != "hierarchy"
                get_role = self.guild.get_role.side_effect
                fetch_member = self.guild.fetch_member.side_effect
                if failure == "missing":
                    self.guild.get_role.side_effect = lambda role_id: None
                if failure == "left-server":
                    self.guild.fetch_member.side_effect = discord.NotFound(
                        SimpleNamespace(status=404, reason="Not Found"), "Unknown Member")
                await subject.approve_application.callback(self.interaction(self.officer, channel), delete_time="1m")
                self.assertEqual(self.store.workflow(record)["status"], "approving")
                self.assertIsNone(self.store.workflow(record)["delete_at"])
                channel.send.assert_not_awaited()
                self.applicant.send.assert_not_awaited()
                self.applicant.add_roles.assert_not_awaited()
                self.guild.get_role.side_effect = get_role
                self.guild.fetch_member.side_effect = fetch_member
        self.assertEqual(self.store.scheduled_deletions(42), [])

    async def test_partial_role_failure_can_resume_after_restart_without_overwriting_decision(self):
        channel = self.channel()
        record = self.register(channel)

        async def partial_failure(*roles, **kwargs):
            self.applicant.roles = [roles[0]]
            raise discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Second role failed")

        self.applicant.add_roles.side_effect = partial_failure
        await subject.approve_application.callback(self.interaction(self.officer, channel), "Original welcome", "1h")
        self.assertEqual(self.store.workflow(record)["status"], "approving")
        self.assertIsNone(self.store.workflow(record)["delete_at"])
        self.applicant.send.assert_not_awaited()
        self.restart()
        self.applicant.add_roles.reset_mock(side_effect=True)
        other_officer = member(2002, officer=True)
        await subject.approve_application.callback(self.interaction(other_officer, channel), "New welcome", "1m")
        self.assertEqual([role.id for role in self.applicant.add_roles.call_args.args], [1548423178794307745])
        workflow = self.store.workflow(record)
        self.assertEqual(workflow["status"], "approved")
        self.assertEqual(workflow["reviewer_id"], self.officer.id)
        self.assertEqual(workflow["decision_note"], "Original welcome")
        self.assertEqual(workflow["delete_at"] - workflow["decided_at"], 3600)

    async def test_database_failure_after_role_grant_is_retryable(self):
        channel = self.channel()
        record = self.register(channel)

        async def grant(*roles, **kwargs):
            self.applicant.roles.extend(roles)

        self.applicant.add_roles.side_effect = grant
        with patch.object(self.store, "finish_decision", side_effect=sqlite3.OperationalError("disk full")):
            await subject.approve_application.callback(self.interaction(self.officer, channel))
        self.assertEqual(self.store.workflow(record)["status"], "approving")
        self.applicant.send.assert_not_awaited()
        self.restart()
        await subject.approve_application.callback(self.interaction(self.officer, channel))
        self.assertEqual(self.applicant.add_roles.await_count, 1)
        self.assertEqual(self.store.workflow(record)["status"], "approved")

    async def test_approval_preserves_existing_roles(self):
        channel = self.channel()
        self.register(channel)
        unrelated = SimpleNamespace(id=999)
        self.applicant.roles = [unrelated, self.approval_roles[0]]
        await subject.approve_application.callback(self.interaction(self.officer, channel))
        self.assertEqual(self.applicant.add_roles.call_args.args, (self.approval_roles[1],))
        self.applicant.remove_roles.assert_not_awaited()

    async def test_concurrent_opposing_decisions_only_complete_one_outcome(self):
        channel = self.channel()
        record = self.register(channel)
        entered, release = asyncio.Event(), asyncio.Event()

        async def held_grant(*roles, **kwargs):
            entered.set()
            await release.wait()

        self.applicant.add_roles.side_effect = held_grant
        approve = asyncio.create_task(subject.approve_application.callback(self.interaction(self.officer, channel)))
        await asyncio.wait_for(entered.wait(), 2)
        reject = asyncio.create_task(subject.reject_application.callback(self.interaction(self.officer, channel)))
        release.set()
        await asyncio.gather(approve, reject)
        self.assertEqual(self.store.workflow(record)["status"], "approved")
        self.assertEqual(channel.send.await_count, 1)
        self.assertEqual(self.applicant.send.await_count, 1)
        self.assertEqual(self.applicant.add_roles.await_count, 1)

    async def test_final_decision_cannot_be_repeated_or_reversed_after_restart(self):
        channel = self.channel()
        record = self.register(channel)
        await subject.reject_application.callback(self.interaction(self.officer, channel), "Original reason")
        self.restart()
        await subject.approve_application.callback(self.interaction(self.officer, channel))
        await subject.reject_application.callback(self.interaction(self.officer, channel), "Different reason")
        self.assertEqual(self.store.workflow(record)["status"], "rejected")
        self.assertEqual(self.store.workflow(record)["decision_note"], "Original reason")
        self.assertEqual(channel.send.await_count, 1)
        self.applicant.add_roles.assert_not_awaited()

    async def test_dm_failure_does_not_lose_decision_or_cleanup(self):
        channel = self.channel()
        record = self.register(channel)
        self.applicant.send.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "DM blocked")
        interaction = self.interaction(self.officer, channel)
        await subject.approve_application.callback(interaction, delete_time="1m")
        workflow = self.store.workflow(record)
        self.assertEqual(workflow["status"], "approved")
        self.assertIsNotNone(workflow["delete_at"])
        self.assertIn("DMs disabled or blocked", interaction.followup.send.call_args.args[0])
        self.assertIn((42, channel.id), subject._deletion_tasks)

    async def test_restored_future_cleanup_uses_saved_deadline_once(self):
        channel, record = self.rejection_with_cleanup(3600)
        deadline = self.store.workflow(record)["delete_at"]
        self.restart()
        with patch.object(subject, "schedule_channel_deletion", AsyncMock()) as schedule:
            subject.restore_cleanup_tasks(self.guild)
            subject.restore_cleanup_tasks(self.guild)
            tasks = list(subject._deletion_tasks.values())
            self.assertEqual(len(tasks), 1)
            await asyncio.gather(*tasks)
            schedule.assert_awaited_once()
            self.assertEqual(schedule.call_args.args[:2], (self.guild, record))
            self.assertAlmostEqual(schedule.call_args.args[2], deadline - time.time(), delta=1)
        channel.delete.assert_not_awaited()

    async def test_overdue_cleanup_runs_after_restart_and_keeps_decision_history(self):
        channel, record = self.rejection_with_cleanup(0)
        self.restart()
        subject.restore_cleanup_tasks(self.guild)
        await asyncio.gather(*list(subject._deletion_tasks.values()))
        channel.delete.assert_awaited_once()
        self.assertIsNone(self.store.get(42, channel.id))
        self.assertEqual(self.store.workflow(record)["status"], "rejected")
        self.assertEqual(self.store.workflow(record)["decision_note"], "Saved decision")
        self.assertIsNone(self.store.workflow(record)["delete_at"])

    async def test_failed_continuation_resumes_after_restart_without_duplicates(self):
        self.fail_send_once(2)
        answers = list(self.long_answer_handler().answers)
        await subject.on_message(self.message("submit"))
        channel = self.created[0]
        record = self.store.for_applicant(42, 1001)[0]
        self.assertIsNone(record.message_id)
        self.assertEqual(len(self.store.workflow(record)["published_message_ids"]), 1)
        self.assertIsNotNone(self.store.get_draft(1001))
        self.assertIn("not every answer was posted", self.applicant.send.call_args.args[0])
        await subject.approve_application.callback(self.interaction(self.officer, channel))
        self.assertEqual(self.store.workflow(record)["status"], "pending")
        self.restart()
        self.applicant.display_name = "A considerably longer display name " * 5
        await subject.on_message(self.message("submit"))
        self.assertEqual(len(self.created), 1)
        parts = len(subject.answer_embeds(subject.questions, answers, "Any title"))
        self.assertGreater(parts, 1)
        self.assertEqual(len(channel.messages), parts)
        self.assertEqual(sum(any(field.name == "Discord ID" for field in message.embeds[0].fields)
                             for message in channel.messages), 1)
        self.assertEqual(self.posted_answers(channel), answers)
        record = self.store.get(42, channel.id)
        self.assertEqual(record.message_id, channel.messages[0].id)
        self.assertIsNone(self.store.get_draft(1001))
        await subject.approve_application.callback(self.interaction(self.officer, channel))
        self.assertEqual(self.store.workflow(record)["status"], "approved")

    async def test_failed_first_message_resumes_from_apply_after_cancel(self):
        self.fail_send_once(1)
        answers = list(self.long_answer_handler().answers)
        await subject.on_message(self.message("submit"))
        channel = self.created[0]
        self.assertEqual(channel.messages, [])
        await subject.on_message(self.message("cancel"))
        self.assertIsNone(self.store.get_draft(1001))
        interaction = self.interaction()
        await subject.ApplicationView().apply.callback(interaction)
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self.posted_answers(channel), answers)
        self.assertEqual(self.store.get(42, channel.id).message_id, channel.messages[0].id)
        self.assertIn("has been posted", interaction.followup.send.call_args.args[0])
        self.assertNotIn(1001, subject.ongoing_applications)

    async def test_edits_after_failed_post_are_flagged_when_saved_answers_are_posted(self):
        self.fail_send_once(2)
        answers = list(self.long_answer_handler().answers)
        await subject.on_message(self.message("submit"))
        await subject.on_message(self.message(f"edit {len(answers)}"))
        await subject.on_message(self.message("A changed final answer."))
        await subject.on_message(self.message("submit"))
        self.assertEqual(self.posted_answers(self.created[0]), answers)
        self.assertIn("first submit", self.applicant.send.call_args.args[0])

    async def test_permission_error_partway_through_post_gives_retry_hint(self):
        self.fail_send_once(2, discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing Access"))
        self.long_answer_handler()
        await subject.on_message(self.message("submit"))
        self.assertIsNone(self.store.for_applicant(42, 1001)[0].message_id)
        self.assertIn("post in your application channel", self.applicant.send.call_args.args[0])

    async def test_branch_database_gains_publication_progress_column(self):
        path = self.db_path.with_name("branch.sqlite3")
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE applications (guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, "
                               "applicant_id INTEGER NOT NULL, message_id INTEGER, PRIMARY KEY (guild_id, channel_id))")
            connection.execute("""
                CREATE TABLE application_workflows (
                    guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, applicant_id INTEGER NOT NULL,
                    questions TEXT NOT NULL DEFAULT '[]', answers TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'pending', reviewer_id INTEGER, decision_note TEXT,
                    decided_at REAL, delete_after INTEGER, delete_at REAL,
                    approval_role_ids TEXT NOT NULL DEFAULT '[]', PRIMARY KEY (guild_id, channel_id)
                )
            """)
            connection.execute("INSERT INTO applications VALUES (42, 2000, 1001, 5000)")
            connection.execute("INSERT INTO application_workflows (guild_id, channel_id, applicant_id, answers) "
                               "VALUES (42, 2000, 1001, '[\"Saved answer\"]')")
        connection.close()
        store = ApplicationStore(path)
        self.addCleanup(store.close)
        workflow = store.workflow(ApplicationRecord(42, 2000, 1001, 5000))
        self.assertEqual(workflow["published_message_ids"], [])
        self.assertEqual(workflow["answers"], ["Saved answer"])


if __name__ == "__main__":
    import unittest
    unittest.main()
