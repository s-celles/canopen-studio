use crate::frame::CanFrame;
use crate::isotp::{IsoTpError, IsoTpReassembler};
use std::collections::HashMap;

pub struct IsoTpManager {
    transfers: HashMap<u32, IsoTpReassembler>,
}

impl Default for IsoTpManager {
    fn default() -> Self {
        Self::new()
    }
}

impl IsoTpManager {
    pub fn new() -> Self {
        Self {
            transfers: HashMap::new(),
        }
    }

    pub fn pending_sources(&self) -> Vec<u32> {
        let mut keys: Vec<u32> = self.transfers.keys().copied().collect();
        keys.sort();
        keys
    }

    pub fn reset(&mut self) {
        self.transfers.clear();
    }

    pub fn process_frame(
        &mut self,
        frame: &CanFrame,
    ) -> Result<(Option<Vec<u8>>, Option<CanFrame>), IsoTpError> {
        let source_id = frame.id;

        let pci_type = if frame.payload().is_empty() {
            0xFF // invalid
        } else {
            (frame.payload()[0] >> 4) & 0x0F
        };

        // If it's a Single Frame or First Frame, we create/reset the transfer
        if pci_type == 0 || pci_type == 1 {
            let tx_fc_id = if source_id > 0x08 {
                source_id - 0x08
            } else {
                source_id
            };
            let mut reassembler = IsoTpReassembler::new(source_id, tx_fc_id);
            let res = reassembler.process_frame(frame);
            if pci_type == 1 && res.is_ok() {
                self.transfers.insert(source_id, reassembler);
            }
            return res;
        }

        // For CF (2), we look up an existing transfer
        if pci_type == 2
            && let Some(reassembler) = self.transfers.get_mut(&source_id)
        {
            let res = reassembler.process_frame(frame);
            if let Ok((Some(_), _)) = res {
                self.transfers.remove(&source_id);
            } else if res.is_err() {
                self.transfers.remove(&source_id);
            }
            return res;
        }

        Ok((None, None))
    }
}
