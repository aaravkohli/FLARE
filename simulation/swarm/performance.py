"""Bounded wall-clock diagnostics; never inputs to simulation decisions/hashes."""
from collections import defaultdict, deque
from contextlib import contextmanager
import time


class Performance:
    def __init__(self):
        self.samples=defaultdict(lambda:deque(maxlen=256))
        self.ticks=deque(maxlen=240); self.previous=None; self.clipped=0.

    @contextmanager
    def measure(self, phase):
        start=time.perf_counter()
        try: yield
        finally: self.samples[phase].append((time.perf_counter()-start)*1000)

    def tick(self, before, after, wall_delta):
        now=time.perf_counter()
        if self.previous is not None: self.ticks.append((now-self.previous,after-before))
        self.previous=now; self.clipped+=max(0.,wall_delta-.25)

    def snapshot(self, playing, requested):
        wall=sum(t[0] for t in self.ticks); simulated=sum(t[1] for t in self.ticks)
        achieved=simulated/wall if playing and wall else None
        timings={}
        for name,values in self.samples.items():
            ordered=sorted(values)
            timings[name]=dict(samples=len(values),mean_ms=sum(values)/len(values),p95_ms=ordered[int((len(ordered)-1)*.95)],max_ms=max(values))
        return dict(status='running' if playing else 'paused',achieved_speed=achieved,
            relative_to_requested=achieved/requested if achieved is not None else None,
            clipped_wall_time_s=self.clipped,timings=timings,sample_limit=256,
            evidence='wall_clock_diagnostics_excluded_from_simulation_hashes')
