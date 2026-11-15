//! The failures that end a grant, and the resources that must let go when
//! one does. Shared by every system: the input services implement
//! `FailClosed` the same way on Linux and Windows.

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LifecycleFailure {
    GrantRequired,
    Revoked,
    ScreenLocked,
    LockStateUnknown,
    UserActive,
    ActivityUnknown,
    VisibleFrameUnavailable,
}

impl LifecycleFailure {
    #[must_use]
    pub const fn code(self) -> &'static str {
        match self {
            Self::GrantRequired => "GRANT_REQUIRED",
            Self::Revoked => "REVOKED",
            Self::ScreenLocked => "SCREEN_LOCKED",
            Self::LockStateUnknown => "LOCK_STATE_UNKNOWN",
            Self::UserActive => "USER_ACTIVE",
            Self::ActivityUnknown => "ACTIVITY_UNKNOWN",
            Self::VisibleFrameUnavailable => "BACKEND_UNAVAILABLE",
        }
    }
}

/// A native resource that must be released the moment the grant stops holding.
///
/// The watchdogs used to express this as a single `AtomicBool`, which is enough
/// while the only "resource" is a test flag. A screencast session is not: it is
/// visible to the user as the sharing indicator, and clearing a boolean does not
/// turn that indicator off. So a resource registers itself here and is told,
/// from the watchdog thread, to let go.
///
/// Implementations must return promptly and must tolerate being called twice.
/// They are invoked while nothing else is locked, but on a 100 ms watchdog:
/// blocking here delays every later revoke.
pub trait FailClosed: Send + Sync {
    fn close_fail_closed(&self, reason: LifecycleFailure);
}
