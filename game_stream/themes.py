"""Palettes shared by the wall, native control bars and preferences."""
PALETTES = {
    'default': dict(background='#151b29', foreground='#e5eaf3', field='#0e1420',
                    border='#46546d', frame='#354159', button='#293750', hover='#3d5277',
                    checked='#236953', disabled='#8791a3'),
    'light': dict(background='#e6e6e6', foreground='#191919', field='#fafafa',
                  border='#a3a3a3', frame='#b3b3b3', button='#d6d6d6', hover='#c4c4c4',
                  checked='#aad7bf', disabled='#737373'),
    'dark': dict(background='#191919', foreground='#e6e6e6', field='#0d0d0d',
                 border='#404040', frame='#333333', button='#262626', hover='#383838',
                 checked='#21573d', disabled='#858585'),
}


def stylesheet(theme):
    c = PALETTES[theme]
    return f'''
        QWidget {{ background: {c['background']}; color: {c['foreground']}; font-size: 12px; }}
        QFrame#pane {{ border: 0; border-radius: 0; }}
        QFrame#hoverBar {{ background: {c['background']}; border: 1px solid {c['frame']}; border-radius: 4px; }}
        QLineEdit, QComboBox, QSpinBox {{ background: {c['field']}; padding: 3px; border: 1px solid {c['border']}; }}
        QPushButton {{ background: {c['button']}; padding: 3px 5px; border-radius: 3px; }}
        QPushButton:hover {{ background: {c['hover']}; }}
        QPushButton:checked {{ background: {c['checked']}; }}
        QPushButton:disabled {{ color: {c['disabled']}; }}
        QTabBar::tab {{ padding: 7px 12px; }}
        QTabBar::tab:selected {{ background: {c['button']}; }}
    '''
