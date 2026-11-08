#!/usr/bin/env perl
# webTEX per-compile resource limits for COMPILE_BACKEND=local (the Docker backend has docker run
# --memory/--cpus instead). Sets POSIX rlimits, which every process the compile starts inherits
# (latexmk, each TeX run, biber, ...), then execs the command, normally `perl sandbox.pl ...`:
#   --as BYTES      address space of each process (RLIMIT_AS): a runaway document fails with
#                   "out of memory" instead of pushing the whole container into the OOM killer
#   --cpu SECONDS   CPU time of each process (RLIMIT_CPU): SIGXCPU, then SIGKILL 5 s later
#   --fsize BYTES   largest file a process may write (RLIMIT_FSIZE): no filling the disk
#   --nice N        lower CPU priority (1..19): a full compile queue yields the CPU to the web
#                   server and the database on the same machine
# and always RLIMIT_CORE 0: a process killed by these limits leaves no core dump in the project.
#
#   perl limits.pl --check                                    exit 0 if the limits can be set here
#   perl limits.pl [--as N] [--cpu N] [--fsize N] [--nice N] -- COMMAND ARGS...
use strict;
use warnings;
use Config;

# setrlimit(2) numbers differ per architecture; the RLIMIT_* resources used here do not.
my %SYS_SETRLIMIT = (x86_64 => 160, aarch64 => 164);
my %SYS_GETRLIMIT = (x86_64 => 97, aarch64 => 163);
my %SYS_SETPRIORITY = (x86_64 => 141, aarch64 => 140);
my %RLIMIT = (cpu => 0, fsize => 1, core => 4, as => 9);

sub fail { print STDERR "webTEX limits: $_[0]\n"; exit 126 }

my $setrlimit = $SYS_SETRLIMIT{ (split /-/, $Config{archname})[0] };
if (@ARGV == 1 && $ARGV[0] eq '--check') {
  exit(defined $setrlimit && $^O eq 'linux' && $Config{ivsize} >= 8 ? 0 : 1);
}
fail("unsupported platform $Config{archname}") unless defined $setrlimit && $^O eq 'linux';

my @limits = ([$RLIMIT{core}, 0, 0]);
my $nice = 0;
while (@ARGV && $ARGV[0] ne '--') {
  my $opt = shift @ARGV;
  my $val = shift @ARGV // fail("missing value for $opt");
  my ($name) = $opt =~ /^--(as|cpu|fsize|nice)$/ or fail("unknown option $opt");
  $val =~ /^\d+$/ && $val > 0 or fail("bad value for $opt: $val");
  if ($name eq 'nice') {
    $nice = $val > 19 ? 19 : $val;
    next;
  }
  # CPU: the hard limit a little above the soft one, so SIGXCPU (catchable) comes first.
  push @limits, [$RLIMIT{$name}, $val, $name eq 'cpu' ? $val + 5 : $val];
}
shift @ARGV;
fail('no command') unless @ARGV;

for my $l (@limits) {
  my ($resource, $soft, $hard) = @$l;
  # struct rlimit { rlim_t rlim_cur; rlim_t rlim_max; } (two unsigned 64-bit values). Without
  # privileges a hard limit can only go down: never ask for more than the current one.
  my $current = pack('QQ', 0, 0);
  syscall($SYS_GETRLIMIT{ (split /-/, $Config{archname})[0] }, $resource, $current) == 0 or fail("getrlimit($resource): $!");
  my (undef, $max) = unpack('QQ', $current);
  $hard = $max if $hard > $max;
  $soft = $hard if $soft > $hard;
  my $rlim = pack('QQ', $soft, $hard);
  syscall($setrlimit, $resource, $rlim) == 0 or fail("setrlimit($resource): $!");
}
# setpriority(PRIO_PROCESS, 0 = this process, nice): inherited like the rlimits.
if ($nice) {
  syscall($SYS_SETPRIORITY{ (split /-/, $Config{archname})[0] }, 0, 0, $nice) == 0 or fail("setpriority: $!");
}
exec { $ARGV[0] } @ARGV or fail("cannot run $ARGV[0]: $!");
