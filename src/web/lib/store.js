const _state = {
  timeline: null,
  about: null,
  selectedDate: null,
  prevSelectedDate: null,
  segmentSelection: null,
};

export const store = {
  get(key) { return _state[key]; },
  set(key, value) { _state[key] = value; },

  selectDay(date) {
    if (_state.selectedDate === date) return;
    _state.prevSelectedDate = _state.selectedDate;
    _state.selectedDate = date;
  },

  canCompare() {
    return (
      _state.selectedDate &&
      _state.prevSelectedDate &&
      _state.selectedDate !== _state.prevSelectedDate
    );
  },
};
