#import <Capacitor/Capacitor.h>
#import <AVFoundation/AVFoundation.h>
#import <MediaPlayer/MediaPlayer.h>
#import <UIKit/UIKit.h>

@interface SystemVolumePlugin : CAPPlugin <CAPBridgedPlugin>
@property (nonatomic, assign) float observedVolume;
@property (nonatomic, assign) BOOL observingOutputVolume;
@property (nonatomic, strong) MPVolumeView *volumeView;
@end

@implementation SystemVolumePlugin

- (NSString *)identifier {
    return @"SystemVolumePlugin";
}

- (NSString *)jsName {
    return @"SystemVolume";
}

- (NSArray<CAPPluginMethod *> *)pluginMethods {
    return @[
        [[CAPPluginMethod alloc] initWithName:@"getCurrentVolume" returnType:CAPPluginReturnPromise],
        [[CAPPluginMethod alloc] initWithName:@"getVolume" returnType:CAPPluginReturnPromise],
        [[CAPPluginMethod alloc] initWithName:@"getCurrentSystemVolume" returnType:CAPPluginReturnPromise]
    ];
}

- (void)load {
    [super load];
    [self ensureVolumeViewAttached];
    [self startVolumeObservation];
}

- (void)dealloc {
    [self stopVolumeObservation];
    if (self.volumeView) {
        dispatch_async(dispatch_get_main_queue(), ^{
            [self.volumeView removeFromSuperview];
        });
    }
}

- (void)ensureVolumeViewAttached {
    if (self.volumeView) return;
    dispatch_async(dispatch_get_main_queue(), ^{
        if (self.volumeView) return;
        self.volumeView = [[MPVolumeView alloc] initWithFrame:CGRectMake(-1000, -1000, 1, 1)];
        self.volumeView.hidden = YES;
        UIWindow *window = nil;
        if (@available(iOS 13.0, *)) {
            NSSet *scenes = [UIApplication sharedApplication].connectedScenes;
            for (UIScene *scene in scenes) {
                if (![scene isKindOfClass:[UIWindowScene class]]) continue;
                UIWindowScene *ws = (UIWindowScene *)scene;
                for (UIWindow *w in ws.windows) {
                    if (w.isKeyWindow) { window = w; break; }
                }
                if (window) break;
            }
        }
        if (!window) window = [UIApplication sharedApplication].keyWindow;
        if (window) [window addSubview:self.volumeView];
    });
}

- (void)startVolumeObservation {
    if (self.observingOutputVolume) return;
    [self ensureVolumeViewAttached];
    AVAudioSession *session = [AVAudioSession sharedInstance];
    NSError *err = nil;
    [session setCategory:AVAudioSessionCategoryPlayback
             withOptions:AVAudioSessionCategoryOptionMixWithOthers
                   error:&err];
    [session setActive:YES error:&err];
    self.observedVolume = session.outputVolume;
    @try {
        [session addObserver:self
                  forKeyPath:@"outputVolume"
                     options:(NSKeyValueObservingOptionNew | NSKeyValueObservingOptionInitial)
                     context:nil];
        self.observingOutputVolume = YES;
    } @catch (NSException *exception) {
        self.observingOutputVolume = NO;
    }
}

- (void)stopVolumeObservation {
    if (!self.observingOutputVolume) return;
    AVAudioSession *session = [AVAudioSession sharedInstance];
    @try {
        [session removeObserver:self forKeyPath:@"outputVolume"];
    } @catch (NSException *exception) {
        // no-op
    }
    self.observingOutputVolume = NO;
}

- (void)observeValueForKeyPath:(NSString *)keyPath
                      ofObject:(id)object
                        change:(NSDictionary<NSKeyValueChangeKey,id> *)change
                       context:(void *)context {
    if ([keyPath isEqualToString:@"outputVolume"]) {
        NSNumber *newValue = change[NSKeyValueChangeNewKey];
        if ([newValue isKindOfClass:[NSNumber class]]) {
            float v = [newValue floatValue];
            if (v < 0.0f) v = 0.0f;
            if (v > 1.0f) v = 1.0f;
            self.observedVolume = v;
            return;
        }
    }
    [super observeValueForKeyPath:keyPath ofObject:object change:change context:context];
}

- (NSNumber *)readOutputVolume {
    [self startVolumeObservation];
    AVAudioSession *session = [AVAudioSession sharedInstance];
    NSError *err = nil;
    [session setActive:NO error:nil];
    [session setCategory:AVAudioSessionCategoryPlayback
             withOptions:AVAudioSessionCategoryOptionMixWithOthers
                   error:&err];
    [session setActive:YES error:&err];
    float v = session.outputVolume;
    if (v < 0.0f) v = 0.0f;
    if (v > 1.0f) v = 1.0f;
    self.observedVolume = v;
    // If iOS reports a stale read, keep the latest observed value.
    if (self.observedVolume >= 0.0f && self.observedVolume <= 1.0f) {
        v = self.observedVolume;
    }
    return @(v);
}

- (void)resolveVolumeForCall:(CAPPluginCall *)call {
    // Give iOS a short moment to refresh outputVolume after activation.
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.20 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
        [call resolve:@{ @"value": [self readOutputVolume] }];
    });
}

- (void)getCurrentVolume:(CAPPluginCall *)call {
    [self resolveVolumeForCall:call];
}

- (void)getVolume:(CAPPluginCall *)call {
    [self resolveVolumeForCall:call];
}

- (void)getCurrentSystemVolume:(CAPPluginCall *)call {
    [self resolveVolumeForCall:call];
}

@end
