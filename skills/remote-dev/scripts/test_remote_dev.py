"""Offline behavioral tests; never connect to enrolled or real SSH hosts."""
import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import uuid

import remote_dev as client
import worker


BINDING = dict(hostname='example.invalid', user='builder', port=22, identity_files=['~/.ssh/example'],
               proxy_jump='', identity_agent='', proxy_command_sha256='')
FACTS = dict(os='Linux', architecture='x86_64', logical_cpus=4, memory_gib=8,
             available_memory_gib=7, free_disk_gib=30, active_jobs=0,
             tools={'python3': '/usr/bin/python3'}, machine_fingerprint='fixture')


def tar_bytes(entries):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w:gz') as tar:
        for name, content, kind in entries:
            info = tarfile.TarInfo(name)
            if kind == 'file':
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
            else:
                info.type = tarfile.SYMTYPE
                info.linkname = content.decode()
                tar.addfile(info)
    return out.getvalue()


class RemoteDevTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='remote-dev-tests-')
        self.base = Path(self.tmp.name)
        self.config = self.base/'hosts.json'
        self.root = self.base/'agent-remote'
        client.save(self.config, {'version': 1, 'hosts': []})

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *args):
        return client.main(client.parser().parse_args(['--config', str(self.config), *args]))

    def enroll(self):
        host = client.entry('fixture')
        host.update(enabled=True, decision='allowed', allowed_tasks=['test'], allow_source_sync=True,
                    connection=copy.deepcopy(BINDING), approved_at='2026-01-01T00:00:00Z', workspace_root=str(self.root))
        client.save(self.config, {'version': 1, 'hosts': [host]})
        return host

    def repo(self):
        repo = self.base/'repo'; repo.mkdir()
        subprocess.run(['git', '-c', 'init.templateDir=', 'init', '-q', str(repo)], check=True)
        (repo/'source.txt').write_text('original\n')
        (repo/'deleted.txt').write_text('remove me\n')
        (repo/'.gitignore').write_text('ignored.txt\n')
        subprocess.run(['git','-C',str(repo),'add','.'],check=True)
        subprocess.run(['git','-C',str(repo),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid',
                        '-c','core.hooksPath=/dev/null','commit','-qm','fixture'],check=True)
        return repo

    def request(self, command='printf success > result.txt', timeout=5):
        data = tar_bytes([('input.txt', b'input', 'file')])
        return dict(action='run', root=str(self.root), job_id=uuid.uuid4().hex,
                    limits=dict(max_jobs=1,min_free_memory_gib=0,min_free_disk_gib=0),
                    archive=base64.b64encode(data).decode(), sha256=hashlib.sha256(data).hexdigest(),
                    command=command, timeout=timeout, jobs=2)

    def test_missing_empty_denied_and_disabled_never_connect(self):
        run_args = ('run','--host','fixture','--task','test','--snapshot','missing','--command-file','missing')
        with patch.object(client,'rpc',side_effect=AssertionError('No network permitted')), \
             patch.object(client,'effective',side_effect=AssertionError('No SSH permitted')):
            with self.assertRaises(ValueError): self.cli(*run_args)
            host = client.entry('fixture')
            client.save(self.config, {'version':1,'hosts':[host]})
            with self.assertRaises(ValueError): self.cli(*run_args)
            self.cli('deny','--host','fixture','--reason','Production')
            with self.assertRaises(ValueError): self.cli(*run_args)
            self.assertEqual(self.cli('select','--task','test')['candidates'],[])
            self.config.unlink()
            with self.assertRaises(ValueError): self.cli(*run_args)

    def test_init_preserves_explicit_denial(self):
        self.cli('deny','--host','fixture','--reason','Do not use')
        result = self.cli('init')
        self.assertFalse(result['created'])
        self.assertEqual(result['registry']['hosts'][0]['decision'],'denied')

    def test_policy_and_connection_binding_are_enforced(self):
        host = self.enroll()
        with patch.object(client,'effective',return_value=BINDING):
            with self.assertRaises(ValueError): client.approved(client.load(self.config),'fixture','build',True)
            host['allow_source_sync']=False
            client.save(self.config, {'version':1,'hosts':[host]})
            with self.assertRaises(ValueError): client.approved(client.load(self.config),'fixture','test',True)
        with patch.object(client,'effective',return_value=dict(BINDING,hostname='changed.invalid')):
            with self.assertRaisesRegex(ValueError,'binding changed'):
                client.approved(client.load(self.config),'fixture')

    def test_invalid_policy_cannot_be_interpreted_as_enabled(self):
        host = self.enroll()
        for change in [{'enabled':'false'},{'decision':'pending'},{'limits':{'max_jobs':0}},{'allowed_tasks':['deploy']}]:
            bad = copy.deepcopy(host); bad.update(change)
            with self.assertRaises(ValueError): client.validate({'version':1,'hosts':[bad]})

    def test_refresh_does_not_restore_a_revoked_permission(self):
        self.enroll()
        def revoke_during_probe(host):
            self.cli('deny','--host','fixture','--reason','Revoked during probe')
            return FACTS
        with patch.object(client,'effective',return_value=BINDING),patch.object(client,'inspect_host',side_effect=revoke_during_probe):
            result=self.cli('refresh','--host','fixture')
        self.assertFalse(result['enabled'])
        self.assertEqual(client.load(self.config)['hosts'][0]['decision'],'denied')
        self.assertEqual(result['facts'],FACTS)

    def test_run_rechecks_revocation_after_preflight(self):
        self.enroll(); repo=self.repo(); archive=self.base/'input.tar.gz'; client.snapshot(repo,archive)
        command=self.base/'command'; command.write_text('true')
        def revoke(host):
            self.cli('deny','--host','fixture','--reason','Revoked before upload')
            return FACTS
        with patch.object(client,'effective',return_value=BINDING),patch.object(client,'inspect_host',side_effect=revoke),patch.object(client,'rpc',side_effect=AssertionError('No upload permitted')):
            with self.assertRaisesRegex(ValueError,'not explicitly enabled'):
                self.cli('run','--host','fixture','--task','test','--snapshot',str(archive),'--command-file',str(command))

    def test_ssh_wrapper_applies_strict_checks_to_jump_hosts(self):
        def fake_ssh(args,**kwargs):
            self.assertEqual(args[:3],['ssh','-T','-F'])
            config=Path(args[3]).read_text()
            self.assertIn('StrictHostKeyChecking yes',config)
            self.assertIn('BatchMode yes',config)
            self.assertIn('ForwardAgent no',config)
            return subprocess.CompletedProcess(args,0,stdout='{"ok":true,"result":{"fixture":true}}',stderr='')
        with patch.object(client.subprocess,'run',side_effect=fake_ssh):
            self.assertTrue(client.rpc('fixture',{'action':'probe','root':str(self.root)})['fixture'])

    def test_unrequested_remote_artifacts_are_rejected(self):
        self.enroll(); payload=b'not requested'
        response={'files':[{'path':'unexpected.txt','sha256':hashlib.sha256(payload).hexdigest(),'content':base64.b64encode(payload).decode()}]}
        destination=self.base/'download'
        with patch.object(client,'effective',return_value=BINDING),patch.object(client,'rpc',return_value=response):
            with self.assertRaisesRegex(ValueError,'requested file list'):
                self.cli('fetch','--host','fixture','--job',uuid.uuid4().hex,'--paths','expected.txt','--output',str(destination))
        self.assertFalse(destination.exists())

    def test_recursive_ssh_includes_and_patterns(self):
        config=self.base/'ssh_config'; includes=self.base/'includes'; includes.mkdir()
        config.write_text('Host explicit explicit *.invalid !deny\nInclude includes/*\n')
        (includes/'one').write_text('Host second\nInclude ssh_config\n')
        self.assertEqual(client.discover_files([config]),['explicit','second'])

    def test_selection_checks_resources_arch_tools_and_duplicate_hosts(self):
        host=self.enroll(); alias=copy.deepcopy(host); alias['alias']='second'
        client.save(self.config,{'version':1,'hosts':[host,alias]})
        with patch.object(client,'effective',return_value=BINDING),patch.object(client,'inspect_host',return_value=FACTS):
            selected=self.cli('select','--task','test','--os','Linux','--arch','x86_64','--tools','python3')
            self.assertEqual(len(selected['candidates']),1)
            self.assertIn('Same machine',selected['rejected'][0]['reasons'][0])
            rejected=self.cli('select','--task','test','--arch','arm64','--memory-gib','20','--tools','node')
            self.assertFalse(rejected['remote_execution_possible'])
            self.assertEqual(len(rejected['rejected'][0]['reasons']),3)
        with patch.object(client,'effective',return_value=BINDING),patch.object(client,'inspect_host',return_value=dict(FACTS,active_jobs=1)):
            self.assertFalse(self.cli('select','--task','test')['remote_execution_possible'])

    def test_snapshot_contains_current_work_and_excludes_credentials(self):
        repo=self.repo(); (repo/'source.txt').write_text('modified\n'); (repo/'deleted.txt').unlink()
        (repo/'new.txt').write_text('new'); (repo/'ignored.txt').write_text('ignored')
        (repo/'.env').write_text('TOKEN=should-not-transfer')
        (repo/'odd-name.txt').write_text('-----BEGIN OPENSSH PRIVATE KEY-----\nsecret')
        (repo/'escape').symlink_to(self.config)
        result=client.snapshot(repo,self.base/'input.tar.gz')
        with tarfile.open(result['archive'],'r:gz') as archive:
            self.assertEqual(set(archive.getnames()),{'.gitignore','source.txt','new.txt'})
            self.assertEqual(archive.extractfile('source.txt').read(),b'modified\n')
        self.assertTrue(result['dirty'])
        self.assertEqual((repo/'source.txt').read_text(),'modified\n')
        self.assertGreaterEqual(len(result['omitted']),3)

    def test_lfs_pointers_are_not_silently_uploaded(self):
        repo=self.repo(); (repo/'large.bin').write_text('version https://git-lfs.github.com/spec/v1\noid sha256:abc\n')
        with self.assertRaisesRegex(ValueError,'LFS'):
            client.snapshot(repo,self.base/'input.tar.gz')

    def test_archive_traversal_links_and_duplicate_names_are_rejected(self):
        destination=self.base/'extract'; destination.mkdir()
        for entries in [[('../escape',b'bad','file')],[('link',b'/tmp','symlink')],
                        [('a',b'one','file'),('a',b'two','file')],[('job.json',b'fake','file')]]:
            with self.assertRaises(ValueError): worker.unpack(tar_bytes(entries),destination)
            self.assertEqual(list(destination.iterdir()),[])

    def test_unmarked_workspace_and_foreign_cleanup_are_rejected(self):
        self.root.mkdir(); (self.root/'unrelated.txt').write_text('keep')
        with self.assertRaises(ValueError): worker.managed_root(str(self.root),create=True)
        self.assertEqual((self.root/'unrelated.txt').read_text(),'keep')
        with self.assertRaises(ValueError): worker.handle(dict(action='cleanup',root=str(self.root),job_id='x'))

    def test_worker_success_fetch_and_cleanup(self):
        request=self.request()
        with patch.object(worker,'facts',return_value=FACTS): result=worker.handle(request)
        self.assertEqual(result['exit_code'],0)
        self.assertEqual(list((self.root/'slots').iterdir()),[])
        fetch=worker.handle(dict(action='fetch',root=str(self.root),job_id=request['job_id'],paths=['result.txt']))
        self.assertEqual(base64.b64decode(fetch['files'][0]['content']),b'success')
        job=self.root/'jobs'/request['job_id']; (job/'link').symlink_to(self.config)
        for name in ('../outside','link'):
            with self.assertRaises(ValueError): worker.handle(dict(action='fetch',root=str(self.root),job_id=request['job_id'],paths=[name]))
        worker.handle(dict(action='cleanup',root=str(self.root),job_id=request['job_id']))
        self.assertFalse(job.exists())

    def test_failure_exit_code_and_timeout_are_distinct(self):
        with patch.object(worker,'facts',return_value=FACTS):
            failed=worker.handle(self.request('echo failure >&2; exit 7'))
            self.assertEqual(failed['exit_code'],7)
            self.assertEqual(failed['state'],'finished')
            sleepy=shlex.quote(sys.executable)+" -c 'import time; time.sleep(20)'"
            timed=worker.handle(self.request(sleepy,timeout=1))
            self.assertEqual(timed['state'],'timed_out')
            self.assertEqual(timed['exit_code'],124)
        self.assertEqual(list((self.root/'slots').iterdir()),[])

    def test_busy_slot_and_running_job_prevent_conflicting_actions(self):
        root=worker.managed_root(str(self.root),create=True)
        slot=root/'slots'/'0'; slot.mkdir(); (slot/'owner').write_text('unrelated-owner')
        with patch.object(worker,'facts',return_value=FACTS):
            with self.assertRaisesRegex(ValueError,'slots are busy'): worker.handle(self.request())
        self.assertEqual((slot/'owner').read_text(),'unrelated-owner')
        job_id=uuid.uuid4().hex; job=root/'jobs'/job_id; job.mkdir()
        worker.write_json(job/worker.JOB_MARKER,{'id':job_id}); worker.write_json(job/'job.json',{'state':'running'})
        with self.assertRaisesRegex(ValueError,'still be running'):
            worker.handle(dict(action='cleanup',root=str(self.root),job_id=job_id))

    def test_end_to_end_client_worker_locally_without_ssh(self):
        self.enroll(); repo=self.repo(); archive=self.base/'input.tar.gz'; client.snapshot(repo,archive)
        commands=self.base/'commands.sh'; commands.write_text('cat source.txt > result.txt\nprintf tested\n')
        def local_rpc(alias,request,timeout=40):
            self.assertEqual(alias,'fixture')
            return worker.handle(request)
        with patch.object(client,'effective',return_value=BINDING), patch.object(client,'rpc',side_effect=local_rpc), patch.object(worker,'facts',return_value=FACTS):
            result=self.cli('run','--host','fixture','--task','test','--snapshot',str(archive),'--command-file',str(commands),'--os','Linux')
            self.assertEqual(result['exit_code'],0)
            output=self.base/'results'
            self.cli('fetch','--host','fixture','--job',result['job_id'],'--paths','result.txt,stdout.log','--output',str(output))
            self.assertEqual((output/'result.txt').read_text(),'original\n')
            self.assertEqual((output/'stdout.log').read_text(),'tested')
            self.cli('cleanup','--host','fixture','--job',result['job_id'])
            self.assertFalse((self.root/'jobs'/result['job_id']).exists())
        self.assertEqual((repo/'source.txt').read_text(),'original\n')


if __name__ == '__main__':
    unittest.main()
