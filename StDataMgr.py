import logging
import struct
import string

import idc
import idautils
import idaapi

import capstone
import unicorn

from flare_emu import flare_emu

from RamPack import RamPack

class StDataMgr():
    def __init__(self, loglevel=logging.INFO):
        self.logger = logging.getLogger("ST_STORE")
        self.logger.setLevel(loglevel)
        self.fe = self.get_flare_emu()
        return

    def _dump(self):
        self.logger.info("ST_DATA_MGR.sLocalTree: 0x{0:x}".format(self.pages_tree()))
        self.logger.info("ST_DATA_MGR.ChunkMetadata: 0x{0:x}".format(self.chunk_metadata()))
        self.logger.info("ST_DATA_MGR.SmkmStore: 0x{0:x}".format(self.smkm_store()))
        self.logger.info("ST_DATA_MGR.RegionSizeMask: 0x{0:x}".format(self.region_size_mask()))
        self.logger.info("ST_DATA_MGR.RegionLSB: 0x{0:x}".format(self.region_lsb()))
        self.logger.info("ST_DATA_MGR.CompressionAlg: 0x{0:x}".format(self.compression_format_and_engine()))
        return

    def sizeof(self):
        return

    def pages_tree(self):
        # appears to always be first entry
        return 0

    def chunk_metadata(self):
        (startAddr, endAddr) = self.locate_call_in_fn("?StDmpSinglePageAdd", "SmHpChunkAlloc")
        self.fe.iterate([endAddr], self.tHook)
        return self.fe.uc.reg_read(unicorn.x86_const.UC_X86_REG_ECX)

    def store_flags(self):
        (fn_addr, fn_name) = self.find_ida_name("?StDmIsCurrentRegion")
        self.fe.emulateRange(fn_addr, instructionHook=self.eHookTrace)
        for t in self.fe.getUserStorage()['trace']:
            t = t['cs_insn']
            if t.mnemonic == "cmp":
                offset = t.operands[0].value.mem.disp
                break
        return offset

    def smkm_store(self):
        (startAddr, endAddr) = self.locate_call_in_fn("?StReleaseRegion", "?SmStReleaseVirtualRegion")
        pat = self.patgen(8192)
        lp_stdatamgr = self.fe.loadBytes(pat)

        def pHook(self, userData, funcStart):
            self.logger.debug("pre emulation hook loading ECX")
            userData['EmuHelper'].uc.reg_write(unicorn.x86_const.UC_X86_REG_ECX, lp_stdatamgr)

        self.fe.iterate([endAddr], self.tHook, preEmuCallback=pHook)
        reg_ecx = self.fe.uc.reg_read(unicorn.x86_const.UC_X86_REG_ECX)
        return pat.find(struct.pack("<I", reg_ecx))

    """
    ST_STORE<SM_TRAITS>::StDmRegionRemove(ST_STORE<SM_TRAITS>::_ST_DATA_MGR *,ulong *)+47   024 mov     eax, [ebx+ST_DATA_MGR.dwRegionMask]
    ST_STORE<SM_TRAITS>::StDmRegionRemove(ST_STORE<SM_TRAITS>::_ST_DATA_MGR *,ulong *)+4D   024 lea     edx, [ebx+20Ch]
    ST_STORE<SM_TRAITS>::StDmRegionRemove(ST_STORE<SM_TRAITS>::_ST_DATA_MGR *,ulong *)+53   024 push    ecx
    ST_STORE<SM_TRAITS>::StDmRegionRemove(ST_STORE<SM_TRAITS>::_ST_DATA_MGR *,ulong *)+54   028 inc     eax
    ST_STORE<SM_TRAITS>::StDmRegionRemove(ST_STORE<SM_TRAITS>::_ST_DATA_MGR *,ulong *)+55   028 push    eax
    ST_STORE<SM_TRAITS>::StDmRegionRemove(ST_STORE<SM_TRAITS>::_ST_DATA_MGR *,ulong *)+56   02C push    ecx
    ST_STORE<SM_TRAITS>::StDmRegionRemove(ST_STORE<SM_TRAITS>::_ST_DATA_MGR *,ulong *)+57   030 push    edi
    ST_STORE<SM_TRAITS>::StDmRegionRemove(ST_STORE<SM_TRAITS>::_ST_DATA_MGR *,ulong *)+58   034 mov     ecx, ebx
    ST_STORE<SM_TRAITS>::StDmRegionRemove(ST_STORE<SM_TRAITS>::_ST_DATA_MGR *,ulong *)+5A   034 call    ?StDmRegionEvict@?$ST_STORE@USM_TRAITS@@@@SGJPAU_ST_DATA_MGR@1@PAU_STDM_SEARCH_RESULTS@1@KKKK@Z ; ST_STORE<SM_TRAITS>::StDmRegionEvict(ST_STORE<SM_TRAITS>::_ST_DATA_MGR *,ST_STORE<SM_TRAITS>::_STDM_SEARCH_RESULTS *,ulong,ulong,ulong,ulong)
    """

    def region_size_mask(self):
        pat = self.patgen(8192)
        lp_stdatamgr = self.fe.loadBytes(pat)

        def pHook(self, userData, funcStart):
            self.logger.debug("pre emulation hook loading ECX")
            userData['EmuHelper'].uc.reg_write(unicorn.x86_const.UC_X86_REG_ECX, lp_stdatamgr)

        (startAddr, endAddr) = self.locate_call_in_fn("?StDmRegionRemove", "?StDmRegionEvict")

        self.fe.iterate([endAddr], self.tHook, preEmuCallback=pHook)
        reg_esp = self.fe.uc.reg_read(unicorn.x86_const.UC_X86_REG_ESP)
        stack_bytes = self.fe.getEmuBytes(reg_esp, 0xC)
        third_arg = stack_bytes[0x8:]
        return pat.find(struct.pack("<I", struct.unpack("<I", third_arg)[0] - 1))

    def region_lsb(self):
        pat = self.patgen(8192)
        lp_stdatamgr = self.fe.loadBytes(pat)
        region_lsb_pattern = {'pattern': 0}

        def pHook(self, userData, funcStart):
            self.logger.debug("pre emulation hook loading ECX")
            userData['EmuHelper'].uc.reg_write(unicorn.x86_const.UC_X86_REG_ECX, lp_stdatamgr)

        # Using an instruction hook because data in offset is difficult to track beyond arithmetic ops like shr
        def iHook(uc, address, size, user_data):
            dis = idc.GetDisasm(address)
            if "shr" in dis:
                # This is the "equivalent" of using nonlocal in py3
                region_lsb_pattern['pattern'] += user_data['EmuHelper'].uc.reg_read(unicorn.x86_const.UC_X86_REG_ECX)

        (startAddr, endAddr) = self.locate_call_in_fn("?StDeviceWorkItemCleanup", "?StRegionReadDereference")
        self.fe.iterate([endAddr], self.tHook, preEmuCallback=pHook, instructionHook=iHook)
        return pat.find(struct.pack("<I", region_lsb_pattern["pattern"]))

    def data_offset_in_compressed_buf(self):
        return 0

    def compression_format_and_engine(self):
        pat = self.patgen(1024, size=2)  # Reduced pattern len & size to detect WORD
        lp_stdatamgr = self.fe.loadBytes(pat)
        region_lsb_pattern = {'pattern': 0}

        def pHook(self, userData, funcStart):
            self.logger.debug("pre emulation hook loading ECX")
            userData['EmuHelper'].uc.reg_write(unicorn.x86_const.UC_X86_REG_ECX, lp_stdatamgr)

        (startAddr, endAddr) = self.locate_call_in_fn("?StDmSinglePageCopy", "_RtlDecompressBufferEx@")
        self.fe.iterate([endAddr], self.tHook, preEmuCallback=pHook)
        reg_esp = self.fe.uc.reg_read(unicorn.x86_const.UC_X86_REG_ESP)
        stack_bytes = self.fe.getEmuBytes(reg_esp, 0x2)  # Using 0x2 because this is a WORD field
        return pat.find(stack_bytes)

    def smcr_integrity(self):
        return

    def region_written_size_array(self):
        return