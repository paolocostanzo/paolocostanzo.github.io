"""host.py: the Q5 rule on the audit log (D36). Run: python3 -m unittest discover -s telemetry/test"""
import importlib.util, tempfile, unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("host", Path(__file__).resolve().parent.parent / "rules/host.py")
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


def ex(t, serial, pid, ppid, exe, argv, cwd):
    a = " ".join(f'a{i}="{v}"' for i, v in enumerate(argv))
    return (f'type=SYSCALL msg=audit({t:.3f}:{serial}): arch=c000003e syscall=59 success=yes exit=0 ppid={ppid} pid={pid} '
            f'uid=1000 comm="{exe.split("/")[-1]}" exe="{exe}" key="exec"\n'
            f'type=EXECVE msg=audit({t:.3f}:{serial}): argc={len(argv)} {a}\n'
            f'type=CWD msg=audit({t:.3f}:{serial}): cwd="{cwd}"\n')


class Host(unittest.TestCase):
    def log(self, *lines):
        p = Path(tempfile.mkdtemp()) / "audit.log"
        p.write_text("".join(lines))
        return p

    def test_a_new_launcher_in_a_project_folder_at_client_start_fires(self):
        p = self.log(ex(100.0, 1, 10, 1, "/usr/bin/codex", ["codex"], "/home/u/repo"),
                     ex(105.0, 2, 11, 10, "/usr/bin/node", ["node", "server.js"], "/home/u/repo"))
        self.assertEqual(host.apply([], p, 90, 200)["host/q5-start"], [105.0])
        self.assertEqual(host.apply(["node server.js"], p, 90, 200)["host/q5-start"], [], "a known command does not fire")

    def test_home_folder_late_start_shell_and_repeat_do_not_fire(self):
        p = self.log(ex(100.0, 1, 10, 1, "/usr/bin/claude", ["claude"], "/home/u/repo"),
                     ex(101.0, 2, 11, 10, "/usr/bin/node", ["node", "a.js"], "/home/u"),            # home folder
                     ex(102.0, 3, 12, 10, "/usr/bin/bash", ["bash", "-c", "ls"], "/home/u/repo"),  # a shell
                     ex(140.0, 4, 13, 10, "/usr/bin/node", ["node", "b.js"], "/home/u/repo"),      # after 30 s
                     ex(103.0, 5, 14, 10, "/usr/bin/npx", ["npx", "srv"], "/home/u/repo"),
                     ex(110.0, 6, 15, 10, "/usr/bin/npx", ["npx", "srv"], "/home/u/repo"))         # seen earlier
        self.assertEqual(host.apply([], p, 90, 200)["host/q5-start"], [103.0])

    def test_exec_time_of_the_named_pid_is_its_last_exec_before_the_record(self):
        p = self.log(ex(100.0, 1, 10, 1, "/usr/bin/codex", ["codex"], "/r"), ex(104.6, 2, 77, 10, "/usr/bin/npx", ["npx"], "/r"),
                     ex(104.8, 3, 77, 10, "/usr/bin/node", ["node"], "/r"), ex(106.0, 4, 77, 10, "/usr/bin/node", ["node"], "/r"))
        self.assertEqual(host.exec_time_of(p, 77, 105.0), 104.8)
        self.assertIsNone(host.exec_time_of(p, 78, 105.0))

    def test_long_arguments_in_pieces_and_launches_before_t0(self):
        long = (f'type=SYSCALL msg=audit(105.000:2): arch=c000003e syscall=59 success=yes exit=0 ppid=10 pid=11 uid=1000 '
                f'comm="node" exe="/usr/bin/node" key="exec"\n'
                f'type=EXECVE msg=audit(105.000:2): argc=2 a0="node" a1_len=8\n'
                f'type=EXECVE msg=audit(105.000:2): a1[0]="serv"\n'
                f'type=EXECVE msg=audit(105.000:2): a1[1]="er.js"\n'
                f'type=CWD msg=audit(105.000:2): cwd="/home/u/repo"\n')
        p = self.log(ex(100.0, 1, 10, 1, "/usr/bin/codex", ["codex"], "/home/u/repo"), long)
        self.assertEqual([c for _, c, _, _, _ in host.launches(host.events(p))], ["node server.js"])
        self.assertEqual(host.apply([], p, 106, 200)["host/q5-start"], [], "a launch before t0 is outside the window")
        self.assertEqual(host.apply([], p, 90, 200)["host/q5-start"], [105.0])

    def test_project_folder(self):
        self.assertTrue(host.PROJECT_RX.match("/home/u/repo/sub"))
        for d in ("/home/u", "/home/u/.config/x", "/", "/tmp/x", "/usr/lib"):
            self.assertFalse(host.PROJECT_RX.match(d), d)


if __name__ == "__main__":
    unittest.main()
