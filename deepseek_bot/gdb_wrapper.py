"""
GDB 批量脚本基础封装 v0.1 — 副线轻量开发
───────────────────────────────────────────
用途: 为 auto-heap-exploit 提供底层 GDB 操控能力
      当前阶段: 基础封装（attach/断点/dump/执行）

后续对接: auto-heap-exploit 的堆布局验证、free list 跟踪

用法:
  from .gdb_wrapper import GDB, GDBProcess, batch_gdb_dump

  gdb = GDB()
  gdb.attach(pid=12345)
  gdb.breakpoint("_efree")
  gdb.continue_exec()
  gdb.dump_memory(addr="0x7f...", size=256)
"""

import subprocess, os, time, tempfile, json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any


# ==================== 数据模型 ====================

@dataclass
class GDBProcess:
    """GDB 附着的目标进程信息"""
    pid: int
    binary_path: str = ""
    libc_path: str = ""
    maps: Dict[str, Any] = field(default_factory=dict)

@dataclass
class MemoryDump:
    """内存 dump 结果"""
    address: str
    size: int
    hex_bytes: str = ""
    ascii_repr: str = ""
    raw_data: bytes = b""

@dataclass
class BreakpointResult:
    """断点命中结果"""
    bp_id: int
    hit_count: int = 0
    registers: Dict[str, str] = field(default_factory=dict)
    backtrace: List[str] = field(default_factory=list)


# ==================== GDB 核心 ====================

class GDB:
    """
    GDB 自动化封装 — 纯 batch 模式，不交互
    
    设计原则:
      • 所有操作通过 gdb -batch -x script.gdb 完成
      • 每次调用独立，不维持持久 session
      • 结果通过临时文件回传（可靠 + 可复用）
    """
    
    def __init__(self, gdb_bin: str = "gdb", timeout: int = 30):
        self.gdb = gdb_bin
        self.timeout = timeout
        self._tmpdir = Path(tempfile.mkdtemp(prefix="gdb_"))
        self._bp_counter = 0
        self._attached_pid: Optional[int] = None
    
    def attach(self, pid: int, binary: str = "") -> GDBProcess:
        """
        附着到进程（不保持session，仅做快速探测）
        返回进程基本信息：maps, libc路径等
        """
        self._attached_pid = pid
        
        script = f"""
set pagination off
attach {pid}
info proc mappings
detach
quit
"""
        out = self._run_script(script)
        
        proc = GDBProcess(pid=pid, binary_path=binary)
        proc.maps = self._parse_maps(out)
        
        # 尝试找到 libc
        for line in out.split("\n"):
            if "libc" in line and ".so" in line:
                parts = line.strip().split()
                if len(parts) >= 5:
                    proc.libc_path = parts[-1]
                    break
        
        return proc
    
    def breakpoint(self, location: str, condition: str = "") -> int:
        """
        设置断点，返回 bp_id。
        location: 函数名("_efree") 或 地址("*0x7f...")
        """
        self._bp_counter += 1
        cond_cmd = f" -condition {condition}" if condition else ""
        
        script = f"""
set pagination off
attach {self._attached_pid}
break {location}
commands {self._bp_counter}
  silent
  printf "BP_HIT:{self._bp_counter}:{location}\\n"
  info registers
  backtrace 10
  continue
end
detach
quit
"""
        self._run_script(script)
        return self._bp_counter
    
    def continue_exec(self, timeout: int = 10) -> str:
        """继续执行并捕获断点输出"""
        script = f"""
set pagination off
attach {self._attached_pid}
continue &
sleep {timeout}
interrupt
detach
quit
"""
        return self._run_script(script, timeout + 10)
    
    def dump_memory(self, address: str, size: int = 256, 
                    output_file: str = "") -> MemoryDump:
        """
        Dump 指定地址的内存。
        address: "0x7f..." 或 "$rsp" 或 "&variable"
        """
        out_file = Path(output_file) if output_file else self._tmpdir / f"dump_{int(time.time())}.bin"
        
        script = f"""
set pagination off
attach {self._attached_pid}
dump memory {out_file} {address} {address}+{size}
detach
quit
"""
        self._run_script(script)
        
        dump = MemoryDump(address=address, size=size)
        
        if out_file.exists():
            dump.raw_data = out_file.read_bytes()
            dump.hex_bytes = dump.raw_data.hex()
            # 简单的 ASCII 表示
            ascii_chars = []
            for b in dump.raw_data[:128]:
                ascii_chars.append(chr(b) if 32 <= b < 127 else ".")
            dump.ascii_repr = "".join(ascii_chars)
        
        return dump
    
    def execute(self, cmd: str) -> str:
        """执行单条 GDB 命令（用于 attach 后的一次性操作）"""
        script = f"""
set pagination off
attach {self._attached_pid}
{cmd}
detach
quit
"""
        return self._run_script(script)
    
    def batch_execute(self, commands: List[str]) -> str:
        """批量执行 GDB 命令"""
        cmds = "\n".join(commands)
        script = f"""
set pagination off
attach {self._attached_pid}
{cmds}
detach
quit
"""
        return self._run_script(script)
    
    def find_pattern(self, pattern: str, start_addr: str = "0x0",
                     end_addr: str = "0xffffffffffffffff") -> List[str]:
        """在内存中搜索模式（如 ROP gadget）"""
        script = f"""
set pagination off
attach {self._attached_pid}
find /b {start_addr}, {end_addr}, {pattern}
detach
quit
"""
        out = self._run_script(script)
        
        addresses = []
        for line in out.split("\n"):
            if "0x" in line:
                for word in line.split():
                    if word.startswith("0x"):
                        addresses.append(word)
        return addresses
    
    def info_registers(self) -> Dict[str, str]:
        """读取当前寄存器状态"""
        out = self.execute("info registers")
        regs = {}
        for line in out.split("\n"):
            parts = line.strip().split()
            if len(parts) >= 2 and parts[0] not in ("", "Using"):
                regs[parts[0]] = parts[1] if len(parts) > 1 else ""
        return regs
    
    def backtrace(self, depth: int = 20) -> List[str]:
        """获取调用栈"""
        out = self.execute(f"backtrace {depth}")
        return [l.strip() for l in out.split("\n") if l.strip() and not l.startswith("Using")]
    
    def symbol_info(self, symbol: str) -> Dict[str, str]:
        """查询符号信息"""
        out = self.execute(f"info symbol {symbol}")
        return {"symbol": symbol, "info": out.strip()}
    
    # ==================== 内部方法 ====================
    
    def _run_script(self, script: str, timeout: int = 0) -> str:
        """写入脚本文件并用 gdb -batch 执行"""
        timeout = timeout or self.timeout
        script_path = self._tmpdir / f"gdb_{int(time.time()*1000)}.gdb"
        script_path.write_text(script)
        
        try:
            p = subprocess.run(
                f"{self.gdb} -batch -x {script_path} 2>&1",
                shell=True, capture_output=True, text=True,
                timeout=timeout
            )
            return (p.stdout or "") + "\n" + (p.stderr or "")
        except subprocess.TimeoutExpired:
            return "[GDB 超时]"
        except Exception as e:
            return f"[GDB 错误: {e}]"
    
    def _parse_maps(self, output: str) -> Dict[str, Any]:
        """解析 /proc/PID/maps 输出"""
        maps = {"segments": [], "libraries": {}}
        
        for line in output.split("\n"):
            if not line.strip() or "Mapped address" in line:
                continue
            
            parts = line.split()
            if len(parts) >= 5:
                addr_range = parts[0]
                perms = parts[1] if len(parts) > 1 else ""
                offset = parts[2] if len(parts) > 2 else ""
                path = parts[-1] if len(parts) > 4 else ""
                
                maps["segments"].append({
                    "range": addr_range,
                    "perms": perms,
                    "offset": offset,
                    "path": path,
                })
                
                # 按库分组
                if path and path != "[stack]" and path != "[heap]":
                    lib_name = Path(path).name
                    if lib_name not in maps["libraries"]:
                        maps["libraries"][lib_name] = []
                    maps["libraries"][lib_name].append({
                        "range": addr_range,
                        "perms": perms,
                    })
        
        return maps
    
    def cleanup(self):
        """清理临时文件"""
        import shutil
        if self._tmpdir.exists():
            shutil.rmtree(self._tmpdir, ignore_errors=True)
    
    def __del__(self):
        self.cleanup()


# ==================== 便捷函数 ====================

def batch_gdb_dump(pid: int, addresses: List[str], size: int = 256) -> List[MemoryDump]:
    """
    批量 dump 多个地址。
    
    Args:
        pid: 目标进程ID
        addresses: 地址列表 ["0x7f...", "0x7f...", "$rsp"]
        size: 每个地址读取的字节数
    
    Returns:
        List[MemoryDump]
    """
    gdb = GDB()
    gdb.attach(pid)
    
    dumps = []
    for addr in addresses:
        dump = gdb.dump_memory(addr, size)
        dumps.append(dump)
    
    return dumps


def gdb_stack_snapshot(pid: int) -> Dict[str, Any]:
    """
    快速抓取进程快照：寄存器 + 调用栈 + 堆栈附近内存。
    用于堆漏洞利用时快速判断堆布局状态。
    """
    gdb = GDB()
    gdb.attach(pid)
    
    return {
        "registers": gdb.info_registers(),
        "backtrace": gdb.backtrace(),
        "pid": pid,
    }
