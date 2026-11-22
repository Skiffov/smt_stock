"""Стани проєкту і дозволені переходи (State pattern)."""

class ProjectState:
    """Behavioral State pattern: each state owns its permitted transitions."""
    def next(self, event):
        raise ValueError(f'Операція {event} недоступна у стані {type(self).__name__}.')

class CreatedState(ProjectState):
    def next(self, event):
        if event=='start': return 'in_production'
        return super().next(event)

class InProductionState(ProjectState):
    def next(self, event):
        if event=='finish': return 'awaiting_return'
        return super().next(event)

class AwaitingReturnState(ProjectState):
    def next(self, event):
        if event=='close': return 'finished'
        return super().next(event)

class FinishedState(ProjectState):
    pass

class ProjectWorkflow:
    STATES = {'created': CreatedState, 'in_production': InProductionState,
              'awaiting_return': AwaitingReturnState, 'finished': FinishedState}

    @classmethod
    def transition(cls, state, event):
        if state not in cls.STATES:
            raise ValueError(f'Невідомий статус проєкту: {state}')
        return cls.STATES[state]().next(event)


