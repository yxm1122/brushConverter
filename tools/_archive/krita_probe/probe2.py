# -*- coding: utf-8 -*-
"""kritarunner 执行语义探测：模块级就打印，验证脚本到底有没有被跑、__name__ 是什么。"""
import os
import sys

print("HELLO_FROM_SCRIPT", flush=True)
sys.stderr.write("STDERR_FROM_SCRIPT\n")
sys.stderr.flush()
print("NAME=%r ARGV=%r CWD=%r" % (__name__, sys.argv, os.getcwd()), flush=True)


def main():
    print("MAIN_CALLED", flush=True)


if __name__ == "__main__":
    main()
