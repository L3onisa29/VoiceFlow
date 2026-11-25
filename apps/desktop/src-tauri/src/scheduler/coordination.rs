//! Locks are kernel-owned open descriptions. Never unlink lock files or trust a pidfile.
use anyhow::Result;
use std::{
    fs::{self, File, OpenOptions},
    os::fd::{AsRawFd, RawFd},
    os::unix::fs::{DirBuilderExt, OpenOptionsExt},
    path::{Path, PathBuf},
    thread,
    time::{Duration, Instant},
};

pub struct Permit(File);
impl Permit {
    pub fn adopt(fd: RawFd, path: &Path) -> Result<Self> {
        use std::os::fd::FromRawFd;
        Self::validate_inherited(fd, path)?;
        // Validated CLI descriptor is uniquely adopted by the launcher.
        Ok(Self(unsafe { File::from_raw_fd(fd) }))
    }
    pub fn validate_inherited(fd: RawFd, path: &Path) -> Result<()> {
        use std::os::unix::fs::MetadataExt;
        anyhow::ensure!(fd >= 3, "Permesso del worker mancante.");
        let mut stat: libc::stat = unsafe { std::mem::zeroed() };
        anyhow::ensure!(
            unsafe { libc::fstat(fd, &mut stat) } == 0,
            "Descrittore del worker non valido."
        );
        let expected = fs::symlink_metadata(path)?;
        anyhow::ensure!(
            expected.is_file()
                && expected.dev() == stat.st_dev as u64
                && expected.ino() == stat.st_ino as u64,
            "Permesso del worker estraneo."
        );
        anyhow::ensure!(
            unsafe { libc::flock(fd, libc::LOCK_EX | libc::LOCK_NB) } == 0,
            "Permesso del worker non posseduto."
        );
        Ok(())
    }
    pub fn try_acquire(path: &Path) -> Result<Option<Self>> {
        Self::try_lock(path, libc::LOCK_EX)
    }
    fn try_lock(path: &Path, mode: i32) -> Result<Option<Self>> {
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW)
            .open(path)?;
        // flock is associated with this open file description, including inherited descriptors.
        if unsafe { libc::flock(file.as_raw_fd(), mode | libc::LOCK_NB) } == 0 {
            Ok(Some(Self(file)))
        } else {
            let error = std::io::Error::last_os_error();
            if error.kind() == std::io::ErrorKind::WouldBlock {
                Ok(None)
            } else {
                Err(error.into())
            }
        }
    }
    pub fn inherit(&self) -> Result<RawFd> {
        let fd = self.0.as_raw_fd();
        if unsafe { libc::fcntl(fd, libc::F_SETFD, 0) } < 0 {
            return Err(std::io::Error::last_os_error().into());
        }
        Ok(fd)
    }
}
#[derive(Clone)]
pub struct Coordination {
    pub(crate) root: PathBuf,
}
impl Coordination {
    pub fn new(data: &Path) -> Self {
        Self {
            root: data.join("analysis-coordination"),
        }
    }
    pub fn initialize(&self) -> Result<()> {
        fs::DirBuilder::new()
            .recursive(true)
            .mode(0o700)
            .create(&self.root)?;
        Ok(())
    }
    pub fn worker(&self) -> Result<Option<Permit>> {
        self.initialize()?;
        Permit::try_acquire(&self.root.join("worker.lock"))
    }
    pub fn work(&self) -> Result<Option<Permit>> {
        Permit::try_acquire(&self.root.join("work.lock"))
    }
    pub fn intent(&self) -> Result<Option<Permit>> {
        Permit::try_acquire(&self.root.join("capture.lock"))
    }
    pub fn recording(&self) -> bool {
        !matches!(
            Permit::try_lock(&self.root.join("capture.lock"), libc::LOCK_SH),
            Ok(Some(_))
        )
    }
    /// Called before model/ASR construction; retained through pause and finalization.
    pub fn capture(&self) -> Result<CapturePermit> {
        self.initialize()?;
        let intent_start = Instant::now();
        let intent = loop {
            if let Some(intent) = self.intent()? {
                break intent;
            }
            if intent_start.elapsed() > Duration::from_millis(100) {
                return Err(anyhow::anyhow!(
                    "Un'altra dettatura possiede il coordinamento."
                ));
            }
            thread::sleep(Duration::from_millis(1));
        };
        let start = Instant::now();
        loop {
            match self.work() {
                Ok(Some(work)) => {
                    return Ok(CapturePermit {
                        _intent: intent,
                        work: Some(work),
                    });
                }
                Err(_) => {
                    return Ok(CapturePermit {
                        _intent: intent,
                        work: None,
                    });
                }
                Ok(None) => (),
            }
            if start.elapsed() >= Duration::from_millis(1500) {
                return Ok(CapturePermit {
                    _intent: intent,
                    work: None,
                });
            }
            thread::sleep(Duration::from_millis(10));
        }
    }
}
pub struct CapturePermit {
    _intent: Permit,
    work: Option<Permit>,
}
impl CapturePermit {
    pub fn degraded(&self) -> bool {
        self.work.is_none()
    }
}
