import importlib.util
import unittest
import json
import queue
import sqlite3
import tempfile
import threading
from pathlib import Path

spec = importlib.util.spec_from_file_location('monitor', Path(__file__).resolve().parents[1] / 'src/monitor.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class DetectorTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.detector = m.Detector(lambda *args: self.events.append(args))
    def turn(self, status='inProgress', items=None):
        return {'title': '库存核对', 'turns': [{'turnId': 'turn1', 'status': status, 'items': items or []}]}
    def test_baseline_skips_old_completions(self):
        self.detector.inspect('local', 'thread1', self.turn('completed', [{'type': 'agentMessage', 'text': '已完成', 'phase': 'final'}]), True)
        self.assertEqual(self.events, [])
    def test_live_completion_and_no_duplicate(self):
        self.detector.inspect('local', 'thread1', self.turn(), True)
        state = self.turn('completed', [{'type': 'agentMessage', 'text': '成本表已完成，请查看结果', 'phase': 'final'}])
        self.detector.inspect('local', 'thread1', state)
        self.detector.inspect('local', 'thread1', state)
        self.assertEqual(len(self.events), 1)
        self.assertIn('成本表', self.events[0][-1])
    def test_approval_uses_reason_without_command(self):
        state = self.turn()
        state['requests'] = [{'id': 'r1', 'method': 'item/commandExecution/requestApproval', 'params': {'reason': '请确认是否部署', 'command': 'SECRET=do-not-speak'}}]
        self.detector.inspect('local', 'thread1', state, True)
        self.assertEqual(self.events[0][-1], '请确认是否部署')
    def test_async_answer_removes_pending_question(self):
        question = {'type': 'agentMessage', 'id': 'call1', 'questions': [{'title': '请选择货件目的地', 'options': ['A仓', 'B仓']}]}
        self.detector.inspect('local', 'thread1', self.turn(items=[question]), True)
        self.assertIn('请选择货件目的地', self.events[-1][-1])
        reply = {'type': 'steeringUserMessage', 'input': [{'type': 'text', 'text': '<send_user_message_question_reply>\n[{"questionItemId":"[\\"request_user_input_async\\",\\"call1\\",0]","answer":"A仓"}]\n</send_user_message_question_reply>'}]}
        self.events.clear()
        self.detector.inspect('local', 'thread1', self.turn(items=[question, reply]))
        self.assertEqual(self.events, [])
    def test_failed_reason(self):
        state = self.turn('failed'); state['turns'][0]['error'] = {'message': '连接超时，需要重新登录'}
        self.detector.inspect('local', 'thread1', state)
        self.assertEqual(self.events[0][-1], '连接超时，需要重新登录')
    def test_heartbeat_silent(self):
        self.detector.inspect('local', 'thread1', self.turn(), True)
        self.detector.inspect('local', 'thread1', self.turn('completed', [{'type': 'agentMessage', 'phase': 'final', 'text': 'HEARTBEAT_OK'}]))
        self.assertEqual(self.events, [])
    def test_canonical_history_patches(self):
        original = {'turnHistory': {'history': {'entitiesByKey': {'tail:1': {'turnId': 't', 'status': 'inProgress', 'items': [{'type': 'commandExecution', 'id': 'c', 'aggregatedOutput': 'secret'}]}}}}}
        state = m.trim_state(original)
        self.assertNotIn('aggregatedOutput', m.turns_from(state)[0]['items'][0])
        m.apply_patches(state, [{'op': 'add', 'path': ['turnHistory', 'history', 'entitiesByKey', 'tail:1', 'items', 1], 'value': {'type': 'agentMessage', 'text': '完成'}}, {'op': 'replace', 'path': ['turnHistory', 'history', 'entitiesByKey', 'tail:1', 'status'], 'value': 'completed'}])
        self.assertEqual(m.final_text(m.turns_from(state)[0]), '完成')
    def test_token_and_command_output_patches_stay_discarded(self):
        state = m.trim_state({'latestTokenUsageInfo': {'last': {}}, 'turns': [{'turnId': 't', 'items': [{'type': 'commandExecution', 'id': 'c', 'aggregatedOutput': 'secret'}]}]})
        m.apply_patches(state, [{'op': 'replace', 'path': ['latestTokenUsageInfo', 'last', 'totalTokens'], 'value': 123}, {'op': 'replace', 'path': ['turns', 0, 'items', 0, 'aggregatedOutput'], 'value': 'more-secret'}])
        self.assertNotIn('latestTokenUsageInfo', state)
        self.assertNotIn('aggregatedOutput', state['turns'][0]['items'][0])
    def test_speech_redaction_and_limit(self):
        spoken = m.speech_text('测试', '等待确认', 'api_key=' + 'sk-' + 'x' * 32 + ' ' + '具体内容。' * 200, 60)
        self.assertNotIn('x' * 32, spoken)
        self.assertIn('完整内容', spoken)
    def test_formal_voice_script_and_sentence_pauses(self):
        segments = m.speech_plan('费用核对', '等待你确认', '费用已核对。\n请确认成本数据，再继续下一步。')
        self.assertIn('需要您确认', segments[1]['text'])
        self.assertNotIn('Codex 提醒。任务：', '\n'.join(s['text'] for s in segments))
        self.assertGreater(segments[1]['pause'], 0.3)
        self.assertGreaterEqual(len(segments), 4)
        self.assertLess(segments[-1]['rateMultiplier'], 1.0)
    def test_list_boundaries_become_sentences(self):
        segments = m.speech_plan('结果', '结果待查看', '- 第一项结果\n- 第二项结果')
        self.assertEqual(segments[2]['text'], '第一项结果。')
        self.assertEqual(segments[3]['text'], '第二项结果。')
    def test_financial_amount_and_id_survive_speech(self):
        spoken = m.speech_text('核对', '结果待查看', '建议价 $21.28。编号 ITEM-0001。FBA 费用已核对。')
        self.assertIn('21.28 美元', spoken)
        self.assertIn('ITEM-0001', spoken)
        self.assertIn('亚马逊仓储配送', spoken)
    def test_role_voice_uses_local_engine_and_continuous_text(self):
        entry = {'title': '费用核对', 'kind': '等待你确认', 'content': '请确认成本数据。'}
        args, payload = m.speech_job({'voice_id': 'local:custom-1'}, entry, Path('/private/test'))
        self.assertEqual(args, ['/private/test/neural-venv/bin/python', '/private/test/NeuralEngine.py'])
        self.assertEqual(payload['voiceId'], 'local:custom-1')
        self.assertEqual(payload['language'], 'Chinese')
        self.assertIn('请确认成本数据', payload['text'])
        self.assertNotIn('segments', payload)
    def test_native_voice_remains_available(self):
        args, payload = m.speech_job({'voice_id': 'com.apple.voice.compact.zh-CN.Tingting'}, {'title':'测试','kind':'测试提醒','content':'请查看任务。'}, Path('/private/test'))
        self.assertEqual(args, ['/private/test/SpeechEngine'])
        self.assertTrue(payload['segments'])
    def test_character_rate_tone_and_style_reach_player(self):
        cfg = {'voice_id': 'local:custom-1', 'rate': 260, 'pitch_semitones': -2, 'speech_style': 'firm'}
        _, payload = m.speech_job(cfg, {'title':'测试','kind':'测试提醒','content':'请查看任务。'}, Path('/private/test'))
        self.assertEqual(payload['rate'], 260)
        self.assertEqual(payload['pitchSemitones'], -2)
        self.assertEqual(payload['style'], 'firm')
        self.assertEqual(payload['settingsPath'], '/private/test/settings.json')
    def test_native_style_changes_real_acoustics(self):
        entry = {'title':'测试','kind':'测试提醒','content':'请查看任务。'}
        _, formal = m.speech_job({'voice_id':'system','speech_style':'formal'}, entry)
        _, firm = m.speech_job({'voice_id':'system','speech_style':'firm','pitch_semitones':-2}, entry)
        self.assertLess(firm['segments'][-1]['pitch'], formal['segments'][-1]['pitch'])
        self.assertGreater(firm['segments'][-1]['rateMultiplier'], formal['segments'][-1]['rateMultiplier'])
        self.assertLess(firm['segments'][-1]['pause'], formal['segments'][-1]['pause'])
    def test_unloaded_unread_index_is_detected(self):
        state = {'electron-thread-read-state-v1': {'version': 1, 'unreadByIdentity': {'account-hash': {'local:host-hash': ['unloaded-thread'], 'remote-control:host-hash': ['remote-thread']}}}}
        self.assertEqual(m.unread_local_ids(state), {'unloaded-thread'})
    def test_no_unread_results(self):
        self.assertEqual(m.unread_local_ids({}), set())
    def test_final_answer_phase_preferred_over_commentary(self):
        turn = {'items': [{'type': 'agentMessage', 'phase': 'final_answer', 'text': '真实结果'}, {'type': 'agentMessage', 'phase': 'commentary', 'text': '其他进度'}]}
        self.assertEqual(m.final_text(turn), '真实结果')
    def test_unread_backlog_from_before_start_speaks_once(self):
        old_codex, old_data = m.CODEX, m.DATA
        with tempfile.TemporaryDirectory() as folder:
            m.CODEX = Path(folder) / 'codex'; m.CODEX.mkdir()
            m.DATA = Path(folder) / 'announcer'; m.DATA.mkdir()
            try:
                (m.CODEX / '.codex-global-state.json').write_text(json.dumps({'electron-thread-read-state-v1': {'unreadByIdentity': {'account': {'local:host': ['tid']}}}}))
                db = sqlite3.connect(m.CODEX / 'thread_history_1.sqlite')
                db.execute('CREATE TABLE thread_turns(thread_id TEXT,turn_id TEXT,status TEXT,error_json TEXT,final_agent_item_id TEXT,rollout_ordinal INTEGER)')
                db.execute('CREATE TABLE thread_items(thread_id TEXT,turn_id TEXT,item_id TEXT,item_json TEXT,item_type TEXT,rollout_ordinal INTEGER)')
                db.execute('INSERT INTO thread_turns VALUES (?,?,?,?,?,?)', ('tid','old-turn','completed',None,'final-id',1))
                db.execute('INSERT INTO thread_items VALUES (?,?,?,?,?,?)', ('tid','old-turn','final-id',json.dumps({'type':'agentMessage','phase':'final_answer','text':'已有未读任务的真实结果'}),'agentMessage',1))
                db.commit(); db.close()
                monitor = m.Monitor.__new__(m.Monitor)
                monitor.catalog = {('local','tid'): '未加载任务'}
                monitor.seen = {}; monitor.history = []; monitor.history_lock = threading.Lock()
                monitor.speeches = queue.PriorityQueue(); monitor.sequence = 0
                monitor.unread_scan(); monitor.unread_scan()
                self.assertEqual(len(monitor.history), 1)
                self.assertEqual(monitor.history[0]['kind'], '待查看（未读）')
                self.assertEqual(monitor.history[0]['content'], '已有未读任务的真实结果')
                self.assertEqual(monitor.speeches.qsize(), 1)
                # A restart restores persistent de-duplication.
                monitor.seen = m.read_json(m.DATA/'seen.json', {})
                monitor.unread_scan()
                self.assertEqual(len(monitor.history), 1)
            finally:
                m.CODEX, m.DATA = old_codex, old_data

if __name__ == '__main__':
    unittest.main()
